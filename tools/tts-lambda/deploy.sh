#!/usr/bin/env bash
# Deploys (or updates) the heft-tts Lambda behind an API Gateway REST API that requires an
# x-api-key header — plain AWS CLI calls, no Terraform, no SAM. Safe to re-run: every step
# creates-if-missing or updates in place, and prints the endpoint + key for tts.json at the end.
#
#   tools/tts-lambda/deploy.sh            # deploy / update
#   tools/tts-lambda/deploy.sh --destroy  # remove the API, key, usage plan, function, role and
#                                         # usage table (the cached MP3s in S3 are left alone)
set -euo pipefail

PROFILE=${PROFILE:-nursepal}
REGION=${REGION:-us-east-2}                 # where the site bucket lives
POLLY_REGION=${POLLY_REGION:-us-east-1}     # de-DE neural/generative voices are not in us-east-2
BUCKET=${BUCKET:-deutsch-heft-a1-851725594636}
PREFIX=${PREFIX:-1bd4d4f22c48ef57}          # the secret folder the site is served from
FN=${FN:-heft-tts}
TABLE=${TABLE:-heft-tts-usage}
ROLE=${ROLE:-heft-tts-lambda}
BUDGET_USD=${BUDGET_USD:-8}                 # Polly spend the Lambda allows per month
MAX_CHARS=${MAX_CHARS:-300}
QUOTA=${QUOTA:-5000}                        # API requests per month the key may make
RATE=${RATE:-1}                             # steady requests per second the key may make
BURST=${BURST:-2}                           # short burst allowance on top of RATE
ORIGINS=${ORIGINS:-"https://${BUCKET}.s3.${REGION}.amazonaws.com,https://${BUCKET}.s3.amazonaws.com,https://s3.${REGION}.amazonaws.com,https://s3.amazonaws.com,http://127.0.0.1:8765,http://localhost:8765"}

aws() { command aws --profile "$PROFILE" --region "$REGION" "$@"; }
HERE=$(cd "$(dirname "$0")" && pwd)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE}"
TABLE_ARN="arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${TABLE}"

if [[ "${1:-}" == "--destroy" ]]; then
  for id in $(aws apigateway get-usage-plans --query "items[?name=='${FN}'].id" --output text); do
    for k in $(aws apigateway get-usage-plan-keys --usage-plan-id "$id" --query 'items[].id' --output text); do
      aws apigateway delete-usage-plan-key --usage-plan-id "$id" --key-id "$k"; done
    for st in $(aws apigateway get-usage-plan --usage-plan-id "$id" --query 'apiStages[].join(`:`,[apiId,stage])' --output text); do
      aws apigateway update-usage-plan --usage-plan-id "$id" --patch-operations "op=remove,path=/apiStages,value=$st" >/dev/null; done
    aws apigateway delete-usage-plan --usage-plan-id "$id"; done
  for id in $(aws apigateway get-api-keys --name-query "$FN" --query "items[?name=='${FN}'].id" --output text); do aws apigateway delete-api-key --api-key "$id"; done
  for id in $(aws apigateway get-rest-apis --query "items[?name=='${FN}'].id" --output text); do aws apigateway delete-rest-api --rest-api-id "$id"; done
  aws lambda delete-function --function-name "$FN" 2>/dev/null || true
  aws dynamodb delete-table --table-name "$TABLE" >/dev/null 2>&1 || true
  aws iam delete-role-policy --role-name "$ROLE" --policy-name heft-tts 2>/dev/null || true
  aws iam detach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE" 2>/dev/null || true
  echo "removed $FN, $TABLE, $ROLE"; exit 0
fi

echo "== role $ROLE"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW_ROLE=1
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name heft-tts --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"polly:SynthesizeSpeech","Resource":"*"},
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::${BUCKET}/${PREFIX}/tts/*"},
 {"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::${BUCKET}","Condition":{"StringLike":{"s3:prefix":"${PREFIX}/tts/*"}}},
 {"Effect":"Allow","Action":["dynamodb:UpdateItem","dynamodb:GetItem"],"Resource":"${TABLE_ARN}"}
]}
JSON
)"

echo "== table $TABLE"
if ! aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --table-name "$TABLE" --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=month,AttributeType=S --key-schema AttributeName=month,KeyType=HASH >/dev/null
fi
aws dynamodb wait table-exists --table-name "$TABLE"

echo "== code"
ZIP=$(mktemp -d)/heft-tts.zip
(cd "$HERE" && zip -q -j "$ZIP" handler.py)
ENV="{\"Variables\":{\"BUCKET\":\"${BUCKET}\",\"PREFIX\":\"${PREFIX}\",\"USAGE_TABLE\":\"${TABLE}\",\"MONTHLY_BUDGET_USD\":\"${BUDGET_USD}\",\"MAX_CHARS\":\"${MAX_CHARS}\",\"POLLY_REGION\":\"${POLLY_REGION}\",\"ALLOWED_ORIGINS\":\"${ORIGINS}\"}}"
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$ZIP" >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --environment "$ENV" --timeout 20 --memory-size 256 >/dev/null
  aws lambda wait function-updated --function-name "$FN"
else
  for i in 1 2 3 4 5 6 7 8 9 10; do   # a brand-new role takes a few seconds to become assumable
    if aws lambda create-function --function-name "$FN" --runtime python3.12 --handler handler.handler \
         --role "$ROLE_ARN" --zip-file "fileb://$ZIP" --timeout 20 --memory-size 256 \
         --environment "$ENV" --description "Deutsch-Heft on-demand Polly (S3-cached)" >/dev/null 2>/tmp/heft-tts-err; then break; fi
    grep -q 'cannot be assumed' /tmp/heft-tts-err && [[ $i -lt 10 ]] && { sleep 5; continue; }
    cat /tmp/heft-tts-err; exit 1
  done
  aws lambda wait function-active --function-name "$FN"
fi
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 3 >/dev/null
rm -f "$ZIP"

echo "== API Gateway (REST, x-api-key required on GET/POST; OPTIONS open for CORS preflight)"
none() { [[ -z "$1" || "$1" == "None" ]]; }
API_ID=$(aws apigateway get-rest-apis --query "items[?name=='${FN}'].id | [0]" --output text)
if none "$API_ID"; then
  API_ID=$(aws apigateway create-rest-api --name "$FN" --description "Deutsch-Heft on-demand Polly" \
    --endpoint-configuration types=REGIONAL --api-key-source HEADER --binary-media-types '*/*' --query id --output text)
fi
ROOT_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --query "items[?path=='/'].id | [0]" --output text)
RES_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --query "items[?path=='/tts'].id | [0]" --output text)
if none "$RES_ID"; then
  RES_ID=$(aws apigateway create-resource --rest-api-id "$API_ID" --parent-id "$ROOT_ID" --path-part tts --query id --output text)
fi
FN_ARN=$(aws lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text)
INTEGRATION="arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${FN_ARN}/invocations"
for M in GET POST OPTIONS; do
  KEYREQ=true; KEYFLAG=--api-key-required
  [[ $M == OPTIONS ]] && { KEYREQ=false; KEYFLAG=--no-api-key-required; }
  aws apigateway put-method --rest-api-id "$API_ID" --resource-id "$RES_ID" --http-method $M \
    --authorization-type NONE $KEYFLAG >/dev/null 2>&1 || \
  aws apigateway update-method --rest-api-id "$API_ID" --resource-id "$RES_ID" --http-method $M \
    --patch-operations op=replace,path=/apiKeyRequired,value=$KEYREQ >/dev/null
  aws apigateway put-integration --rest-api-id "$API_ID" --resource-id "$RES_ID" --http-method $M \
    --type AWS_PROXY --integration-http-method POST --uri "$INTEGRATION" >/dev/null
done
aws lambda add-permission --function-name "$FN" --statement-id apigw-invoke --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/*/tts" >/dev/null 2>&1 || true
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name v1 >/dev/null
URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/v1/tts"

echo "== API key + usage plan (${RATE} req/s, burst ${BURST}, ${QUOTA} req/month)"
KEY_ID=$(aws apigateway get-api-keys --name-query "$FN" --query "items[?name=='${FN}'].id | [0]" --output text)
if none "$KEY_ID"; then KEY_ID=$(aws apigateway create-api-key --name "$FN" --enabled --query id --output text); fi
KEY=$(aws apigateway get-api-key --api-key "$KEY_ID" --include-value --query value --output text)
PLAN_ID=$(aws apigateway get-usage-plans --query "items[?name=='${FN}'].id | [0]" --output text)
if none "$PLAN_ID"; then
  PLAN_ID=$(aws apigateway create-usage-plan --name "$FN" --throttle rateLimit=${RATE},burstLimit=${BURST} \
    --quota limit=${QUOTA},period=MONTH --api-stages apiId=${API_ID},stage=v1 --query id --output text)
  aws apigateway create-usage-plan-key --usage-plan-id "$PLAN_ID" --key-id "$KEY_ID" --key-type API_KEY >/dev/null
else
  aws apigateway update-usage-plan --usage-plan-id "$PLAN_ID" --patch-operations \
    "op=replace,path=/throttle/rateLimit,value=${RATE}" "op=replace,path=/throttle/burstLimit,value=${BURST}" \
    "op=replace,path=/quota/limit,value=${QUOTA}" "op=replace,path=/quota/period,value=MONTH" >/dev/null
fi

echo "== bucket CORS (lets a locally served copy read the S3 cache directly)"
aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration '{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["GET","HEAD"],"AllowedHeaders":["*"],"ExposeHeaders":["ETag"],"MaxAgeSeconds":86400}]}'

echo
echo "endpoint: $URL"
echo "key:      $KEY"
echo "tts.json next to index.html:  {\"endpoint\": \"$URL\", \"key\": \"$KEY\"}"
