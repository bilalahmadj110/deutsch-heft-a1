"""apply_tts.py — adds the on-demand Polly layer to index.html (voice select in the Hören panel,
script#tts-js + style#tts-css, and the two playback hooks). Idempotent: exits early if the page
already has it. Re-run after anything regenerates index.html (e.g. the Piper batch scripts).
    python3 tools/tts-lambda/apply_tts.py [path/to/index.html]"""
import io, os, sys
p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', '..', 'index.html')
p = os.path.abspath(p)
s = io.open(p, encoding='utf-8').read()
UPDATE = 'id="tts-js"' in s   # already applied → just swap the script/style block for this version

def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, (c, old[:80])
    s = s.replace(old, new)

# 1. panel markup: voice select + status line (first application only; an update keeps the markup)
if not UPDATE: rep('''    <label><input type="checkbox" id="a-auto"> Karteikarten automatisch vorlesen <span class="en-h">(auto-play flashcards)</span></label>
''', '''    <label><input type="checkbox" id="a-auto"> Karteikarten automatisch vorlesen <span class="en-h">(auto-play flashcards)</span></label>
    <label class="a-voice">Stimme <span class="en-h">(voice)</span>
      <select id="a-voice" aria-label="Stimme (voice)">
        <option value="piper">Eingebaut &middot; Piper Thorsten (kostenlos)</option>
        <optgroup label="Amazon Polly &middot; Standard">
          <option value="standard|Vicki">Polly Standard &middot; Vicki</option>
          <option value="standard|Marlene">Polly Standard &middot; Marlene</option>
          <option value="standard|Hans">Polly Standard &middot; Hans</option>
        </optgroup>
        <optgroup label="Amazon Polly &middot; Neural">
          <option value="neural|Vicki">Polly Neural &middot; Vicki</option>
          <option value="neural|Daniel">Polly Neural &middot; Daniel</option>
        </optgroup>
        <optgroup label="Amazon Polly &middot; Generative">
          <option value="generative|Vicki">Polly Generative &middot; Vicki</option>
          <option value="generative|Daniel">Polly Generative &middot; Daniel</option>
          <option value="generative|Lennart">Polly Generative &middot; Lennart</option>
        </optgroup>
      </select>
    </label>
    <p class="a-tts" id="a-tts"></p>
''')

# 2. the HeftTTS module, right after the MANIFEST/PARTS script and before the audio script
TTS = r'''
<style id="tts-css">
  .a-voice select { flex: 1; min-width: 0; font: inherit; font-size: .9rem; padding: .35rem .5rem; border: 1px solid var(--line); border-radius: 6px; background: var(--paper-2); color: var(--ink); }
  .a-tts { font-size: .85rem; color: var(--ink-2); margin: 0; }
  .a-tts.err { color: var(--die); }
  .tts-busy { opacity: .55; cursor: progress; }
</style>
<script id="tts-js">
/* tts.js — on-demand Amazon Polly voices. The Hören panel's "Stimme" select picks the built-in
   Piper clips (default, free, offline) or a Polly engine + voice. With Polly on, every German that is
   read aloud goes through HeftTTS.resolve(text): the page hashes engine|voice|text (SHA-256) and first
   tries the S3 cache next to this file — tts/<engine>/<voice>/<hash>.mp3, served for free — and only on
   a miss POSTs to the heft-tts Lambda (tools/tts-lambda/), which synthesises the sentence once, stores
   the MP3 under that same key and returns it. Nothing is pre-rendered; each sentence is paid for once.
   The Lambda sits behind an API Gateway that wants an x-api-key header; endpoint and key come from
   tts.json next to index.html (kept out of git). Without it the Polly options are disabled and
   everything stays on Piper. The Diktat always uses the Piper parts. */
(function () {
  'use strict';
  var KEY = 'heft-tts-v1', MAX = 300;
  var VOICES = { standard: ['Vicki', 'Marlene', 'Hans'], neural: ['Vicki', 'Daniel'], generative: ['Vicki', 'Daniel', 'Lennart'] };
  var RATE = { standard: 4, neural: 16, generative: 30 };   /* micro-USD per character, list price */
  var st = { engine: 'piper', voice: '' };
  try { var s0 = JSON.parse(localStorage.getItem(KEY) || 'null'); if (s0 && s0.engine) st = { engine: String(s0.engine), voice: String(s0.voice || '') }; } catch (e) {}
  if (!VOICES[st.engine] || VOICES[st.engine].indexOf(st.voice) < 0) { st.engine = 'piper'; st.voice = ''; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }

  var cfg = null, budget = false, lastErr = '', perm = null;   /* perm = tts/stats.json snapshot (all-time + month) */
  var stats = { hits: 0, misses: 0, chars: 0, micro: 0 };
  var ready = fetch('tts.json', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function (j) { if (!j || !j.endpoint || !j.key) throw new Error('kein endpoint'); cfg = j; })
    .catch(function () { cfg = null; })
    .then(function () { render(); pullStats(); });

  function norm(t) { var s = String(t); if (s.normalize) s = s.normalize('NFC'); return s.replace(/ /g, ' ').replace(/\s+/g, ' ').trim(); }
  function sha256(s) {
    if (!(window.crypto && crypto.subtle && window.TextEncoder)) return Promise.reject(new Error('no crypto.subtle'));
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)).then(function (buf) {
      var a = new Uint8Array(buf), h = '';
      for (var i = 0; i < a.length; i++) h += (a[i] < 16 ? '0' : '') + a[i].toString(16);
      return h;
    });
  }
  var mem = Object.create(null);   /* engine|voice|text → object URL, for this page load */

  function active() { return st.engine !== 'piper' && !!cfg && !budget; }

  /* → Promise<url>. Rejects when Polly could not deliver; callers then fall back to the Piper clip. */
  function resolve(text) {
    var t = norm(text), engine = st.engine, voice = st.voice, mk = engine + '|' + voice + '|' + t;
    if (!active()) return Promise.reject(new Error('inactive'));
    if (!t || t.length > MAX) return Promise.reject(new Error('length'));
    if (mem[mk]) return Promise.resolve(mem[mk]);
    var base = cfg.cache ? String(cfg.cache).replace(/\/?$/, '/') : '';
    return sha256(mk).then(function (h) {
      return fetch(base + 'tts/' + engine + '/' + voice + '/' + h + '.mp3').then(function (r) {
        if (!r.ok) throw new Error('S3 ' + r.status);
        stats.hits++; return r.blob();
      });
    }).catch(function () {
      return fetch(cfg.endpoint, { method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': cfg.key },
                                   body: JSON.stringify({ text: t, engine: engine, voice: voice }) })
        .then(function (r) {
          if (r.status === 429) { budget = true; throw new Error('Monatsbudget erreicht'); }
          if (!r.ok) throw new Error('Lambda ' + r.status);
          if (r.headers.get('x-heft-cache') === 'miss') { stats.misses++; stats.chars += t.length; stats.micro += t.length * RATE[engine]; setTimeout(function () { pullStats(true); }, 1500); }
          else stats.hits++;
          return r.blob();
        });
    }).then(function (b) {
      lastErr = ''; render();
      var u = URL.createObjectURL(b); mem[mk] = u; return u;
    }, function (e) { lastErr = String((e && e.message) || e); render(); throw e; });
  }

  /* permanent counters: tts/stats.json (written by the Lambda after every synthesis, a plain S3 read);
     the API's ?stats=1 is the fallback while the file does not exist yet */
  var statsAt = 0;
  function pullStats(force) {
    if (!cfg || (!force && Date.now() - statsAt < 30000)) return;
    statsAt = Date.now();
    var base = cfg.cache ? String(cfg.cache).replace(/\/?$/, '/') : '';
    fetch(base + 'tts/stats.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .catch(function () {
        if (st.engine === 'piper') return null;
        return fetch(cfg.endpoint + (cfg.endpoint.indexOf('?') < 0 ? '?' : '&') + 'stats=1', { headers: { 'x-api-key': cfg.key } })
          .then(function (r) { return r.ok ? r.json() : null; });
      })
      .then(function (j) { if (j && j.total && j.month) { perm = j; render(); } })
      .catch(function () {});
  }
  function mmss(sec) { var m = Math.floor(sec / 60), s2 = Math.round(sec % 60); return m + ':' + (s2 < 10 ? '0' : '') + s2 + ' min'; }
  function permLine(j) {
    var t = j.total, m = j.month;
    return 'Polly gesamt, dauerhaft gezählt: ' + t.clips + ' Sätze erzeugt · ' + t.chars + ' Zeichen · ≈ ' + mmss(t.seconds) + ' Audio · ≈ '
      + t.usd.toFixed(4) + ' $ (geschätzt nach Listenpreis). Diesen Monat: ' + m.clips + ' Sätze, ' + m.usd.toFixed(4) + ' $ von ' + j.budget_usd + ' $.'
      + ' (Stand ' + String(j.updated).replace('T', ' ').replace('Z', ' UTC') + ')';
  }

  function label() { return st.engine === 'piper' ? 'piper' : st.engine + '|' + st.voice; }
  function render() {
    var sel = document.getElementById('a-voice'), note = document.getElementById('a-tts'), tot = document.getElementById('a-tts-total');
    if (note && !tot) { tot = document.createElement('p'); tot.id = 'a-tts-total'; tot.className = 'a-tts'; note.parentNode.insertBefore(tot, note.nextSibling); }
    if (tot) tot.textContent = perm && cfg ? permLine(perm) : '';
    if (sel) {
      Array.prototype.forEach.call(sel.querySelectorAll('optgroup'), function (g) { g.disabled = !cfg; });
      if (sel.value !== label()) sel.value = label();
    }
    if (!note) return;
    var msg, err = false;
    if (st.engine === 'piper') {
      msg = cfg ? 'Eingebaute Stimme (Piper · Thorsten): kostenlos, offline. Polly-Stimmen werden pro Satz beim ersten Anhören erzeugt und gespeichert, danach kostenlos.'
                : 'Eingebaute Stimme (Piper · Thorsten). Polly ist hier nicht eingerichtet — tts.json (endpoint + key) fehlt neben index.html.';
    } else if (budget) {
      msg = 'Polly: Monatsbudget erreicht — bis zum Monatsende liest die eingebaute Stimme vor.'; err = true;
    } else {
      msg = 'Amazon Polly · ' + st.engine + ' · ' + st.voice + '. Diese Sitzung: ' + stats.misses + ' neu erzeugt (' + stats.chars + ' Zeichen ≈ '
          + (stats.micro / 1e6).toFixed(4) + ' $), ' + stats.hits + ' aus dem Speicher.';
      if (lastErr) { msg += ' Letzter Fehler: ' + lastErr + ' — eingebaute Stimme benutzt.'; err = true; }
    }
    note.textContent = msg; note.className = 'a-tts' + (err ? ' err' : '');
  }

  var sel = document.getElementById('a-voice'), open = document.getElementById('audio-open');
  if (sel) sel.addEventListener('change', function () {
    var v = sel.value.split('|');
    if (v.length === 2 && VOICES[v[0]] && VOICES[v[0]].indexOf(v[1]) >= 0) { st.engine = v[0]; st.voice = v[1]; } else { st.engine = 'piper'; st.voice = ''; }
    lastErr = ''; save(); render(); pullStats();
  });
  if (open) open.addEventListener('click', function () { setTimeout(function () { render(); pullStats(); }, 0); });
  render();

  window.HeftTTS = { active: active, resolve: resolve, ready: ready, state: function () { return { engine: st.engine, voice: st.voice }; } };
})();
</script>
'''
if UPDATE:
    a = s.index('<style id="tts-css">'); b = s.index('</script>', s.index('<script id="tts-js">')) + len('</script>')
    s = s[:a] + TTS.strip('\n') + s[b:]
    io.open(p, 'w', encoding='utf-8').write(s)
    print('updated tts block:', p); sys.exit(0)
rep('''var PARTS=[''', '''var PARTS=[''')  # sanity: the manifest script exists once
anchor = s.index('var PARTS=[')
end = s.index('</script>', anchor) + len('</script>')
assert s[end:end+9] == '\n<script>', repr(s[end:end+10])
s = s[:end] + '\n' + TTS + s[end:]

# 3. main audio script: cancel token in stop(), Polly-first say()
rep('''      running = false; queue = []; qi = -1;
      try { audio.pause(); audio.currentTime = 0; } catch (e) {}''',
    '''      running = false; queue = []; qi = -1; sayTok++;
      try { audio.pause(); audio.currentTime = 0; } catch (e) {}''')
rep('''    function say(text, el, done) {
      var src = srcFor(text);
      if (!src) { if (done) done(); return false; }
      playSrc(src, el, done);
      return true;
    }''',
    '''    var sayTok = 0;
    function say(text, el, done) {
      var T = window.HeftTTS;
      if (T && T.active()) {          /* Polly on: fetch or synthesise first; the Piper clip is the fallback */
        var my = ++sayTok;
        highlight(el); if (el) el.classList.add('tts-busy');
        T.resolve(text).then(function (u) {
          if (el) el.classList.remove('tts-busy');
          if (my === sayTok) playSrc(u, el, done);
        }, function () {
          if (el) el.classList.remove('tts-busy');
          if (my !== sayTok) return;
          var src = srcFor(text);
          if (src) playSrc(src, el, done); else { highlight(null); if (done) done(); }
        });
        return true;
      }
      var src = srcFor(text);
      if (!src) { if (done) done(); return false; }
      playSrc(src, el, done);
      return true;
    }''')

# 4. word cards / dialogues: Polly first, then the old chain (Wiktionary → clip → device voice)
rep('''    function fromClip() { if (my !== tok) return; var c = clipFor(t); if (c) playUrl(c, function () { tts(t); }, my); else tts(t); }
    if (single) wikt(word, function (u) { if (my !== tok) return; if (u) playUrl(u, fromClip, my); else fromClip(); });
    else fromClip();
  }''',
    '''    function fromClip() { if (my !== tok) return; var c = clipFor(t); if (c) playUrl(c, function () { tts(t); }, my); else tts(t); }
    function fromWikt() { if (single) wikt(word, function (u) { if (my !== tok) return; if (u) playUrl(u, fromClip, my); else fromClip(); }); else fromClip(); }
    var T = window.HeftTTS;
    if (T && T.active()) T.resolve(t).then(function (u) { if (my === tok) playUrl(u, fromWikt, my); }, fromWikt);
    else fromWikt();
  }''')

io.open(p, 'w', encoding='utf-8').write(s)
print('applied:', p)
