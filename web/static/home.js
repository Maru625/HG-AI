/* 홈 화면 상호작용: 팀 고르기(야근 칸 그림), 직급·단계 고르기, 반려 사유 표시, 절감 계산. 모두 홈에 실린 더미 데이터로만 계산한다. */
(function () {
  'use strict';
  var el = document.getElementById('dash-data');
  if (!el) return;
  var D = JSON.parse(el.textContent);
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function fmt(n, d) { if (!d) n = Math.round(n); return Number(n).toLocaleString('ko-KR', { maximumFractionDigits: d || 0, minimumFractionDigits: d || 0 }); }
  function press(group, btn) { group.forEach(function (b) { b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'); }); }

  // 숫자를 바꿀 때 짧게 굴린다(사용자가 고른 결과를 보여 주는 움직임만 쓴다)
  function tween(node, to, digits) {
    var from = parseFloat(String(node.textContent).replace(/,/g, '')) || 0;
    if (reduce || from === to) { node.textContent = fmt(to, digits); return; }
    var t0 = null, dur = 420;
    function step(t) {
      if (!t0) t0 = t;
      var k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmt(from + (to - from) * e, digits);
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* 1. 히어로: 야근 1시간 = 칸 하나 */
  var ot = D.overtime, cats = ot.categories;
  var CLS = { '전결 처리': 'c-jy', '본업': 'c-work', '회의': 'c-meet', '기타 사무': 'c-etc' };
  var COLOR = { 'c-jy': '#f37321', 'c-work': '#5c5c5c', 'c-meet': '#444', 'c-etc': '#333' };
  var ORDER = ['전결 처리', '본업', '회의', '기타 사무'].filter(function (c) { return cats.indexOf(c) >= 0; });
  var strip = $('#hx-strip'), legend = $('#hx-legend');

  function sums(dept) {
    var out = {};
    ORDER.forEach(function (c) {
      var i = cats.indexOf(c), v = 0;
      (dept ? [dept] : ot.depts).forEach(function (d) { v += ot.hours[d][i] || 0; });
      out[c] = v;
    });
    return out;
  }
  function drawHero(dept, first) {
    var s = sums(dept), all = 0;
    ORDER.forEach(function (c) { all += s[c]; });
    var jy = s['전결 처리'] || 0;
    tween($('[data-k="all"]'), all);
    tween($('[data-k="jy"]'), jy);
    tween($('[data-k="pct"]'), all ? jy / all * 100 : 0);
    $('[data-k="scope"]').textContent = dept || (ot.depts.length + '개 팀');

    var html = '', n = 0, total = 0;
    ORDER.forEach(function (c) { total += Math.round(s[c]); });
    ORDER.forEach(function (c) {
      var k = Math.round(s[c]), cls = CLS[c] || 'c-etc';
      for (var j = 0; j < k; j++) {
        // 주황 칸은 왼쪽 위부터 차례로 켜진다(처음 한 번의 연출)
        html += '<i class="' + cls + '"' + (cls === 'c-jy' ? ' style="--d:' + (reduce ? 0 : Math.min(1.6, n * 0.008)).toFixed(3) + 's"' : '') + '></i>';
        n++;
      }
    });
    strip.classList.remove('lit');
    strip.innerHTML = html;
    if (first && !reduce) { requestAnimationFrame(function () { setTimeout(function () { strip.classList.add('lit'); }, 350); }); }
    else { strip.classList.add('lit'); }

    legend.innerHTML = ORDER.map(function (c) {
      return '<li><i style="background:' + COLOR[CLS[c] || 'c-etc'] + '"></i>' + c + ' <b>' + fmt(s[c]) + '시간</b></li>';
    }).join('');
  }
  var chips = $$('.hx-chip');
  chips.forEach(function (b) {
    b.addEventListener('click', function () { press(chips, b); drawHero(b.getAttribute('data-dept'), false); });
  });
  drawHero('', true);

  /* 2. 단계: 직급별 주당 시간, 단계를 누르면 설명 */
  var al = D.allocation, sv = D.savings, rank = D.survey.ranking;
  var grade = '선임', stageI = 1;
  var maxH = 0;
  al.grades.forEach(function (g) { al.hours[g].forEach(function (v) { if (v > maxH) maxH = v; }); });
  var stepBtns = $$('.jr-btn'), gradeBtns = $$('.pick');

  function drawJourney() {
    var hs = al.hours[grade];
    stepBtns.forEach(function (b, i) {
      $('.jr-bar i', b).style.setProperty('--h', (hs[i] / maxH * 100).toFixed(1) + '%');
      $('.jr-h', b).textContent = fmt(hs[i], 1) + '시간';
      b.setAttribute('aria-label', al.stages[i] + ', 주당 ' + fmt(hs[i], 1) + '시간');
    });
    var jt = al.jeongyeol_total[grade], wt = al.week_total[grade];
    $('#jr-total').innerHTML = '<b>' + grade + '</b>은 주 ' + fmt(wt, 1) + '시간 중 <b>' + fmt(jt, 1) + '시간(' + fmt(jt / wt * 100) + '%)</b>을 결재 서류에 씁니다.';
    drawDetail();
  }
  function drawDetail() {
    var st = al.stages[stageI], h = al.hours[grade][stageI], s = sv.by_stage[stageI] || {};
    var ri = -1;
    rank.forEach(function (r, i) { if (r.option === st) ri = i; });
    var r = ri >= 0 ? rank[ri] : null;
    $('#jr-detail').innerHTML =
      '<div><p class="k">' + (stageI + 1) + '단계</p><h3>' + st + '</h3><p class="v"><b>' + fmt(h, 1) + '</b>시간 / 주 <span class="muted">(' + grade + ')</span></p>' +
        (al.desc ? '<p class="v muted small">하는 일: ' + al.desc[stageI] + '</p>' : '') + '</div>' +
      '<div><p class="k">하기 싫은 업무 설문 (' + D.survey.n + '명)</p><p class="v">' +
        (r ? '<b>' + (ri + 1) + '위</b> · 1순위로 꼽은 사람 ' + r.first + '명' : '설문 항목 아님') + '</p></div>' +
      '<div><p class="k">이 시스템이 맡는 부분</p><p class="v fix">' + (s.feature || '') + '</p>' +
        (s.rate ? '<p class="v muted small">' + fmt(s.rate * 100) + '% 절감 가정</p>' : '') + '</div>';
  }
  gradeBtns.forEach(function (b) {
    b.addEventListener('click', function () { press(gradeBtns, b); grade = b.getAttribute('data-grade'); drawJourney(); });
  });
  stepBtns.forEach(function (b, i) {
    b.addEventListener('click', function () { press(stepBtns, b); stageI = i; drawDetail(); });
  });
  drawJourney();

  /* 3. 반려 점 그림: 사유를 누르면 그 사유의 건만 켠다 */
  var rj = D.rejection, dots = $('#rj-dots');
  if (dots) {
    var html = '', idx = 0;
    rj.reasons.forEach(function (r, ri) { for (var j = 0; j < r.count; j++) html += '<i class="rj" data-r="' + ri + '"></i>'; idx += r.count; });
    for (var k = idx; k < rj.total; k++) html += '<i></i>';
    dots.innerHTML = html;
    var rBtns = $$('.reason');
    rBtns.forEach(function (b) {
      b.addEventListener('click', function () {
        var on = b.getAttribute('aria-pressed') !== 'true';
        press(rBtns, on ? b : null);
        var ri = b.getAttribute('data-r');
        dots.classList.toggle('focus', on);
        $$('i.rj', dots).forEach(function (d) { d.classList.toggle('on', on && d.getAttribute('data-r') === ri); });
      });
    });
  }

  /* 5. 절감 계산 */
  var boxes = $$('#sv input[type=checkbox]'), out = $('#sv-min');
  function drawSave() {
    var m = 0;
    boxes.forEach(function (c) { if (c.checked) m += parseFloat(c.getAttribute('data-min')) || 0; });
    tween(out, Math.round(m));
  }
  boxes.forEach(function (c) { c.addEventListener('change', drawSave); });
})();
