// 회의실 가위바위보 대결 (재미 기능). 모든 화면에서 /duel/state를 주기적으로 보고, 지금 사용자의 대결이 있으면 대결 창을 띄운다.
// 두 사람이 각자 화면에서 고르면 서버가 판정한다. 상대의 선택은 둘 다 고르기 전까지 서버가 알려 주지 않는다.
(function () {
  'use strict';
  var root = document.getElementById('duel-root');
  if (!root || !window.fetch) return;

  var HANDS = { rock: '✊', scissors: '✌️', paper: '✋' };
  var NAMES = { rock: '바위', scissors: '가위', paper: '보' };
  var ORDER = ['scissors', 'rock', 'paper'];
  var reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var cur = null;            // 마지막으로 그린 대결 상태
  var shell = null;          // 대결 창 틀(같은 대결이면 다시 만들지 않는다)
  var busy = false;          // 연출 중이면 갱신을 미룬다
  var timer = 0;

  function keep(kind) {
    return {
      get: function (k) { try { return window[kind].getItem(k); } catch (e) { return null; } },
      set: function (k, v) { try { window[kind].setItem(k, v); } catch (e) { /* 저장소를 못 쓰면 연출이 한 번 더 나올 뿐이다 */ } }
    };
  }
  var session = keep('sessionStorage');
  var local = keep('localStorage');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function later(ms) { return new Promise(function (ok) { setTimeout(ok, reduce ? Math.round(ms * 0.8) : ms); }); }

  // ───────── 소리 (파일 없이 합성) ─────────
  var sound = (function () {
    var ctx = null;
    function ac() {
      if (local.get('duel-mute') === '1') return null;
      if (!ctx) {
        var C = window.AudioContext || window.webkitAudioContext;
        if (!C) return null;
        try { ctx = new C(); } catch (e) { return null; }
      }
      if (ctx.state === 'suspended') { ctx.resume(); }
      return ctx;
    }
    function tone(freq, at, dur, type, vol, slideTo) {
      var c = ac(); if (!c) return;
      var t = c.currentTime + at, o = c.createOscillator(), g = c.createGain();
      o.type = type || 'square';
      o.frequency.setValueAtTime(freq, t);
      if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t + dur);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(vol || 0.15, t + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(g); g.connect(c.destination);
      o.start(t); o.stop(t + dur + 0.03);
    }
    function noise(at, dur, vol, from, to) {
      var c = ac(); if (!c) return;
      var t = c.currentTime + at, n = Math.max(1, Math.floor(c.sampleRate * dur));
      var buf = c.createBuffer(1, n, c.sampleRate), d = buf.getChannelData(0);
      for (var i = 0; i < n; i++) { d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 2); }
      var src = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain();
      src.buffer = buf; f.type = 'lowpass';
      f.frequency.setValueAtTime(from || 3200, t); f.frequency.exponentialRampToValueAtTime(to || 140, t + dur);
      g.gain.value = vol || 0.6;
      src.connect(f); f.connect(g); g.connect(c.destination); src.start(t);
    }
    return {
      unlock: ac,
      chant: function (i) { tone([392, 494, 659][i] || 523, 0, 0.2, 'square', 0.18); noise(0, 0.14, 0.4); },
      boom: function () { noise(0, 0.7, 1.0, 2400, 60); tone(140, 0, 0.6, 'sawtooth', 0.28, 38); },
      win: function () {
        [523, 659, 784, 1047, 784, 1047, 1319].forEach(function (f, i) { tone(f, i * 0.11, i === 6 ? 0.7 : 0.13, 'square', 0.15); });
        for (var k = 0; k < 5; k++) { noise(0.8 + k * 0.18, 0.25, 0.35, 6000, 800); }
      },
      lose: function () { [392, 370, 349, 330].forEach(function (f, i) { tone(f, i * 0.34, i === 3 ? 1.0 : 0.3, 'sawtooth', 0.12, i === 3 ? 247 : null); }); },
      tie: function () { tone(660, 0, 0.12, 'triangle', 0.22); tone(660, 0.16, 0.12, 'triangle', 0.22); tone(990, 0.32, 0.35, 'triangle', 0.22); },
      pick: function () { tone(880, 0, 0.07, 'square', 0.12); tone(1320, 0.07, 0.09, 'square', 0.1); },
      alarm: function () { for (var i = 0; i < 4; i++) { tone(988, i * 0.24, 0.12, 'square', 0.13); tone(740, i * 0.24 + 0.12, 0.12, 'square', 0.13); } }
    };
  })();
  document.addEventListener('pointerdown', function () { sound.unlock(); }, { capture: true });

  // ───────── 꽃가루·불꽃·비 (캔버스) ─────────
  var fx = (function () {
    var cv = null, g = null, parts = [], raf = 0, W = 0, H = 0;
    var COLORS = ['#f37321', '#ffd400', '#ffffff', '#1fb6ff', '#ff3d7f', '#fbb584', '#3dff9a'];
    function size() {
      if (!cv) return;
      var r = window.devicePixelRatio || 1;
      W = cv.clientWidth; H = cv.clientHeight;
      cv.width = Math.max(1, W * r); cv.height = Math.max(1, H * r);
      g.setTransform(r, 0, 0, r, 0, 0);
    }
    window.addEventListener('resize', size);
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function add(p) { parts.push(p); if (!raf) { raf = requestAnimationFrame(loop); } }
    function loop() {
      raf = 0;
      if (!g) { parts = []; return; }
      g.clearRect(0, 0, W, H);
      var next = [];
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.life -= 1;
        p.vy += p.grav; p.vx *= p.drag; p.vy *= p.drag;
        p.x += p.vx; p.y += p.vy; p.rot += p.vr;
        if (p.life <= 0 || p.y > H + 60) continue;
        var a = Math.min(1, p.life / 30);
        g.save(); g.globalAlpha = a; g.translate(p.x, p.y); g.rotate(p.rot);
        if (p.kind === 'rect') { g.fillStyle = p.color; g.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.rot * 2))); }
        else if (p.kind === 'spark') { g.fillStyle = p.color; g.beginPath(); g.arc(0, 0, p.w, 0, Math.PI * 2); g.fill(); }
        else if (p.kind === 'text') { g.font = p.w + 'px "Segoe UI Emoji", "Apple Color Emoji", sans-serif'; g.textAlign = 'center'; g.fillText(p.text, 0, 0); }
        g.restore();
        next.push(p);
      }
      parts = next;
      if (parts.length) { raf = requestAnimationFrame(loop); } else { g.clearRect(0, 0, W, H); }
    }
    function scale(n) { return reduce ? Math.ceil(n / 3) : n; }
    return {
      attach: function (c) { cv = c; g = c.getContext('2d'); parts = []; size(); },
      detach: function () { cv = null; g = null; parts = []; },
      cannon: function (fromLeft, n) {
        for (var i = 0; i < scale(n); i++) {
          add({ kind: 'rect', x: fromLeft ? -10 : W + 10, y: H * 0.85, vx: (fromLeft ? 1 : -1) * rnd(6, 17), vy: -rnd(12, 26), grav: 0.42, drag: 0.992,
                rot: rnd(0, 6), vr: rnd(-0.3, 0.3), w: rnd(8, 14), h: rnd(12, 20), color: COLORS[i % COLORS.length], life: rnd(140, 220) });
        }
      },
      rainConfetti: function (n) {
        for (var i = 0; i < scale(n); i++) {
          add({ kind: 'rect', x: rnd(0, W), y: rnd(-H * 0.6, -10), vx: rnd(-1.5, 1.5), vy: rnd(1, 4), grav: 0.06, drag: 0.995,
                rot: rnd(0, 6), vr: rnd(-0.2, 0.2), w: rnd(7, 12), h: rnd(10, 18), color: COLORS[i % COLORS.length], life: rnd(220, 320) });
        }
      },
      burst: function (x, y, n, colors) {
        var cs = colors || COLORS;
        for (var i = 0; i < scale(n); i++) {
          var ang = (Math.PI * 2 * i) / n + rnd(-0.1, 0.1), sp = rnd(4, 13);
          add({ kind: 'spark', x: x == null ? W / 2 : x, y: y == null ? H / 2 : y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp, grav: 0.12, drag: 0.965,
                rot: 0, vr: 0, w: rnd(2.5, 5.5), color: cs[i % cs.length], life: rnd(50, 90) });
        }
      },
      fireworks: function (count) {
        var self = this;
        for (var k = 0; k < (reduce ? 2 : count); k++) {
          setTimeout(function () { self.burst(rnd(W * 0.15, W * 0.85), rnd(H * 0.12, H * 0.45), 46); }, k * 260);
        }
      },
      sadRain: function (n) {
        for (var i = 0; i < scale(n); i++) {
          add({ kind: 'text', text: i % 3 ? '💧' : '😭', x: rnd(0, W), y: rnd(-H, -20), vx: rnd(-0.4, 0.4), vy: rnd(2, 5), grav: 0.05, drag: 1,
                rot: rnd(-0.3, 0.3), vr: 0, w: rnd(22, 44), life: 400 });
        }
      }
    };
  })();

  // ───────── 서버 호출 ─────────
  function call(path, body) {
    return fetch(path, {
      method: body === undefined ? 'GET' : 'POST', cache: 'no-store', credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  // ───────── 그리기 ─────────
  function animKey(du) { return 'duel-anim-' + du.id + '-' + du.me.emp_no; }
  function fxKey(du) { return 'duel-fx-' + du.id + '-' + du.me.emp_no; }
  function minKey(du) { return 'duel-min-' + du.id + '-' + du.me.emp_no; }

  function openShell(du) {
    if (shell && shell.getAttribute('data-id') === String(du.id)) return;
    closeShell();
    root.innerHTML = '';
    shell = document.createElement('div');
    shell.className = 'dl-overlay';
    shell.setAttribute('role', 'dialog');
    shell.setAttribute('aria-modal', 'true');
    shell.setAttribute('aria-labelledby', 'dl-title');
    shell.setAttribute('data-id', String(du.id));
    shell.innerHTML = '<canvas class="dl-fx" aria-hidden="true"></canvas><div class="dl-flash" aria-hidden="true"></div>'
      + '<div class="dl-stage"></div><div class="dl-caption" aria-live="assertive"></div>';
    root.appendChild(shell);
    fx.attach(shell.querySelector('.dl-fx'));
    var badge = document.querySelector('.dl-badge');
    if (badge) badge.remove();
  }
  function closeShell() {
    fx.detach();
    if (shell) { shell.remove(); shell = null; }
  }
  function stage() { return shell && shell.querySelector('.dl-stage'); }
  function caption(text, cls) {
    if (!shell) return;
    var c = shell.querySelector('.dl-caption');
    c.innerHTML = text ? '<b class="' + (cls || '') + '">' + text + '</b>' : '';
  }
  function flash() {
    if (!shell) return;
    var f = shell.querySelector('.dl-flash');
    f.classList.remove('on'); void f.offsetWidth; f.classList.add('on');
  }
  function shake() {
    var s = stage(); if (!s) return;
    s.classList.remove('shake'); void s.offsetWidth; s.classList.add('shake');
  }

  function sideHtml(cls, tag, name, hand, status, extra) {
    return '<div class="dl-side ' + cls + ' ' + (extra || '') + '"><span class="dl-tag">' + tag + '</span>'
      + '<div class="dl-name">' + esc(name) + '</div><div class="dl-hand' + (hand.ask ? ' ask' : '') + (hand.fly ? ' ' + hand.fly : '') + '">'
      + hand.icon + '</div><div class="dl-status">' + status + '</div></div>';
  }
  function tags(du) {
    return du.role === 'challenger' ? ['도전자 · 나', '수비자 · 기존 예약'] : ['수비자 · 나', '도전자'];
  }
  function header(du) {
    var muted = local.get('duel-mute') === '1';
    return '<button type="button" class="dl-mute" data-act="mute" aria-label="' + (muted ? '소리 켜기' : '소리 끄기') + '">' + (muted ? '🔇' : '🔊') + '</button>'
      + '<span class="dl-demo">DEMO · 재미 기능 (규정 밖)</span>'
      + '<h2 id="dl-title" class="dl-title"><span>회의실</span><span>쟁탈전</span></h2>'
      + '<div class="dl-belt">🏆 ' + esc(du.room) + ' · ' + esc(du.slot) + (du.title ? ' <small>‘' + esc(du.title) + '’ 자리</small>' : '') + '</div>';
  }
  function history(du) {
    if (!du.rounds.length) return '';
    return '<div class="dl-history">' + du.rounds.map(function (r) {
      return '<span>' + r.round + '판 ' + HANDS[r.me] + ' vs ' + HANDS[r.opp] + ' · ' + ({ win: '이김', lose: '짐', tie: '비김' })[r.result] + '</span>';
    }).join('') + '</div>';
  }

  function renderPlay(du) {
    var s = stage(); if (!s) return;
    var t = tags(du), ties = du.rounds.filter(function (r) { return r.result === 'tie'; }).length;
    var mine = du.my_pick;
    var intro = du.role === 'defender' && !du.rounds.length && !mine
      ? '<div class="dl-msg">🚨 <b>' + esc(du.opponent.name) + '</b>이 내 회의실 예약을 걸고 가위바위보 도전장을 보냈습니다! 지면 예약이 넘어갑니다.</div>'
      : '';
    var msg = mine
      ? '<div class="dl-msg"><b>' + HANDS[mine] + ' ' + NAMES[mine] + '</b>를 냈습니다. ' + (du.opp_picked ? '상대도 골랐습니다! 곧 공개합니다' : esc(du.opponent.name) + '이 고르는 중<span class="dots"></span>')
        + '<br><span style="font-size:14px;opacity:.85">상대가 화면을 열면 대결 창이 뜹니다. 시연할 때는 오른쪽 위 사용자 전환에서 ' + esc(du.opponent.name) + '으로 바꾸세요.</span></div>'
      : '<div class="dl-msg">' + (du.round > 1 ? '비겼습니다! 한 판 더. ' : '') + '셋 중 하나를 고르세요. 고르면 바꿀 수 없습니다.</div>';
    var picks = mine ? '' : '<div class="dl-picks" role="group" aria-label="가위바위보 고르기">' + ORDER.map(function (k) {
      return '<button type="button" class="dl-pick" data-pick="' + k + '" aria-label="' + NAMES[k] + '">' + HANDS[k] + '<span>' + NAMES[k] + '</span></button>';
    }).join('') + '</div>';
    var acts = '<div class="dl-actions">'
      + (du.role === 'challenger' ? '<button type="button" class="dl-btn ghost" data-act="cancel">도전 취소</button>' : '<button type="button" class="dl-btn ghost" data-act="yield">양보하기 (예약을 넘겨줌)</button>')
      + '<button type="button" class="dl-btn ghost" data-act="later">나중에</button></div>';
    s.innerHTML = header(du)
      + '<div class="dl-arena">'
      + sideHtml('me', t[0], du.me.name, mine ? { icon: HANDS[mine] } : { icon: '❔', ask: true }, mine ? '선택 완료!' : '고르는 중')
      + '<div class="dl-vs" aria-hidden="true">VS</div>'
      + sideHtml('opp', t[1], du.opponent.name, { icon: du.opp_picked ? '🫣' : '❔', ask: !du.opp_picked }, du.opp_picked ? '선택 완료!' : '고르는 중<span class="dots"></span>')
      + '</div>'
      + '<div class="dl-round">' + du.round + '판째' + (ties ? ' · 앞에서 ' + ties + '번 비김' : '') + '</div>'
      + intro + picks + msg + history(du) + acts;
    var first = s.querySelector('.dl-pick');
    if (first) first.focus({ preventScroll: true });
  }

  function renderResult(du, celebrate) {
    var s = stage(); if (!s || !shell) return;
    var t = tags(du), last = du.rounds[du.rounds.length - 1];
    var canceled = du.status === 'CANCELED';
    var meHand = last ? HANDS[last.me] : (du.won ? '😎' : '🏳️');
    var oppHand = last ? HANDS[last.opp] : (du.won ? '🏳️' : '😎');
    shell.classList.toggle('win', !canceled && du.won);
    shell.classList.toggle('lose', !canceled && !du.won);
    var title = canceled ? '대결 취소' : (du.won ? '이겼다!!!' : '졌다…');
    s.innerHTML = header(du)
      + '<div class="dl-arena">'
      + sideHtml('me', t[0], du.me.name, { icon: meHand }, canceled ? '' : (du.won ? '승리!' : '패배'), canceled ? '' : (du.won ? 'won' : 'ko'))
      + '<div class="dl-vs" aria-hidden="true">VS</div>'
      + sideHtml('opp', t[1], du.opponent.name, { icon: oppHand }, canceled ? '' : (du.won ? '패배' : '승리!'), canceled ? '' : (du.won ? 'ko' : 'won'))
      + '</div>'
      + '<div class="dl-round">' + esc(title) + (du.how === 'yield' ? ' · 양보' : (du.rounds.length ? ' · ' + du.rounds.length + '판 만에 결판' : '')) + '</div>'
      + '<div class="dl-msg">' + esc(du.result_text) + '</div>' + history(du)
      + '<div class="dl-actions">' + (du.next ? '<button type="button" class="dl-btn main" data-act="next" data-href="' + esc(du.next.href) + '">' + esc(du.next.label) + '</button>' : '')
      + '<button type="button" class="dl-btn ' + (du.next ? 'ghost' : 'main') + '" data-act="close">확인</button></div>';
    var btn = s.querySelector('.dl-btn.main');
    if (btn) btn.focus({ preventScroll: true });
    if (celebrate && !canceled) {
      if (du.won) {
        sound.win(); caption('이겼다!!!', 'win'); fx.cannon(true, 90); fx.cannon(false, 90); fx.fireworks(6); fx.rainConfetti(120);
        setTimeout(function () { fx.cannon(true, 60); fx.cannon(false, 60); }, 900);
        setTimeout(function () { caption(''); }, 2200);
      } else {
        sound.lose(); caption('졌다…', 'lose'); fx.sadRain(70);
        setTimeout(function () { caption(''); }, 2200);
      }
    }
  }

  function reveal(du, round) {
    if (!shell) return Promise.resolve();
    busy = true;
    var t = tags(du);
    var s = stage();
    if (s) {
      s.innerHTML = header(du) + '<div class="dl-arena">'
        + sideHtml('me', t[0], du.me.name, { icon: '✊', ask: true }, '두구두구')
        + '<div class="dl-vs" aria-hidden="true">VS</div>'
        + sideHtml('opp', t[1], du.opponent.name, { icon: '✊', ask: true }, '두구두구') + '</div>'
        + '<div class="dl-round">' + round.round + '판 공개!</div>';
    }
    shell.classList.add('fast');
    var words = ['가위!', '바위!', '보!!!'];
    var p = Promise.resolve();
    words.forEach(function (w, i) {
      p = p.then(function () { caption(w, i === 1 ? 'alt' : ''); sound.chant(i); shake(); return later(560); });
    });
    return p.then(function () {
      caption('');
      var st = stage();
      if (st) {
        var hands = st.querySelectorAll('.dl-hand');
        hands[0].className = 'dl-hand fly-l'; hands[0].textContent = HANDS[round.me];
        hands[1].className = 'dl-hand fly-r'; hands[1].textContent = HANDS[round.opp];
        var stat = st.querySelectorAll('.dl-status');
        stat[0].textContent = NAMES[round.me]; stat[1].textContent = NAMES[round.opp];
      }
      return later(300);
    }).then(function () {
      sound.boom(); flash(); shake(); fx.burst(null, null, 70);
      caption('쾅!', 'alt');
      return later(650);
    }).then(function () {
      if (shell) shell.classList.remove('fast');
      if (round.result === 'tie') {
        sound.tie(); caption('비겼다!!<br>한 판 더!', 'small'); fx.rainConfetti(40);
        return later(1700);
      }
      caption(round.result === 'win' ? '이 판은 내가!' : '이 판은 상대가!', 'small ' + (round.result === 'win' ? 'win' : 'lose'));
      return later(900);
    }).then(function () { caption(''); busy = false; }, function () { busy = false; });
  }

  function showBadge(du) {
    closeShell();
    if (document.querySelector('.dl-badge')) return;
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'dl-badge';
    b.innerHTML = '⚔️ 가위바위보 대결 중 · 열기';
    b.addEventListener('click', function () { session.set(minKey(du), ''); b.remove(); cur = null; poll(true); });
    document.body.appendChild(b);
  }

  function apply(st) {
    var du = st && st.duel;
    if (!du) {
      closeShell(); cur = null;
      var b = document.querySelector('.dl-badge'); if (b) b.remove();
      return Promise.resolve();
    }
    if (du.status === 'WAITING' && session.get(minKey(du)) === String(du.rev)) { showBadge(du); cur = du; return Promise.resolve(); }
    if (cur && shell && cur.id === du.id && cur.rev === du.rev) return Promise.resolve();
    var isNew = !shell || shell.getAttribute('data-id') !== String(du.id);
    openShell(du);
    var done = parseInt(session.get(animKey(du)) || '0', 10);
    var p = Promise.resolve();
    if (du.rounds.length > done) {
      session.set(animKey(du), String(du.rounds.length));
      p = reveal(du, du.rounds[du.rounds.length - 1]);
    } else if (isNew && du.status === 'WAITING' && du.role === 'defender' && !du.rounds.length && !du.my_pick) {
      sound.alarm(); caption('⚔ 도전장!', 'alt'); setTimeout(function () { caption(''); }, 1300);
    }
    return p.then(function () {
      cur = du;
      if (du.status === 'WAITING') { renderPlay(du); return; }
      var first = session.get(fxKey(du)) !== '1';
      session.set(fxKey(du), '1');
      renderResult(du, first);
    });
  }

  function act(e) {
    var t = e.target.closest('[data-pick], [data-act]');
    if (!t || !cur || busy) return;
    var du = cur;
    if (t.hasAttribute('data-pick')) {
      sound.unlock(); sound.pick();
      Array.prototype.forEach.call(shell.querySelectorAll('.dl-pick'), function (b) { b.disabled = true; });
      call('/duel/' + du.id + '/pick', { pick: t.getAttribute('data-pick') }).then(function (v) {
        if (v && v.ok === false) { alertMsg(v.message); return; }
        return apply({ duel: v });
      });
      return;
    }
    var a = t.getAttribute('data-act');
    if (a === 'mute') {
      local.set('duel-mute', local.get('duel-mute') === '1' ? '0' : '1');
      t.textContent = local.get('duel-mute') === '1' ? '🔇' : '🔊';
    } else if (a === 'later') {
      session.set(minKey(du), String(du.rev)); showBadge(du);
    } else if (a === 'cancel' || a === 'yield') {
      call('/duel/' + du.id + '/' + a, {}).then(function (v) { if (v && v.ok === false) { alertMsg(v.message); return; } return apply({ duel: v }); });
    } else if (a === 'close' || a === 'next') {
      var href = t.getAttribute('data-href');
      call('/duel/' + du.id + '/seen', {}).then(function () {
        closeShell(); cur = null;
        if (a === 'next' && href) { window.location.href = href; } else { window.location.reload(); }
      });
    }
  }
  function alertMsg(m) {
    var s = stage(); if (!s) return;
    var box = s.querySelector('.dl-msg');
    if (box) box.innerHTML = '<b>' + esc(m || '처리하지 못했습니다.') + '</b>';
  }
  root.addEventListener('click', act);

  function schedule(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
  function poll(now) {
    if (!now && (document.hidden || busy)) { schedule(1500); return; }
    call('/duel/state').then(function (st) { return apply(st); }).catch(function () { /* 다음 주기에 다시 본다 */ })
      .then(function () { schedule(cur && cur.status === 'WAITING' ? 1500 : 3000); });
  }
  window.duelPoll = poll;
  poll(true);
})();
