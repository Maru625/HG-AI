// 홈 대시보드 그래프. 데이터는 페이지의 <script id="dash-data">에서 읽는다.
(function () {
  if (!window.Chart) return;
  var INK2 = '#4a4a4a', INK3 = '#767676', GRID = '#e6e6e6', SURFACE = '#ffffff';
  // 범주 색은 고정 순서로 쓴다. 전결 처리만 한화 오렌지로 강조하고 나머지는 회색 단계(밝기로 구분). 원인 범주 → 색은 항상 같다.
  var CAUSE_COLOR = { '전결 처리': '#f37321', '본업': '#4a4a4a', '회의': '#8c8c8c', '기타 사무': '#c4c4c4' };
  var CAUSE_ORDER = ['전결 처리', '본업', '회의', '기타 사무'];
  var SINGLE = '#f37321';

  Chart.defaults.color = INK2;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 13;
  Chart.defaults.plugins.tooltip.backgroundColor = '#000000';
  Chart.defaults.plugins.tooltip.padding = 8;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;

  // 막대 끝 값 라벨 (단일 계열 순위 막대에만)
  var endLabels = {
    id: 'endLabels',
    afterDatasetsDraw: function (chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      var ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = INK2;
      ctx.font = '600 11px ' + Chart.defaults.font.family;
      ctx.textBaseline = 'middle';
      chart.getDatasetMeta(0).data.forEach(function (bar, i) {
        var v = chart.data.datasets[0].data[i];
        ctx.fillText(opts.fmt ? opts.fmt(v) : v, bar.x + 6, bar.y);
      });
      ctx.restore();
    }
  };
  Chart.register(endLabels);

  var charts = [];
  function hbarOpts(extra) {
    var o = {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
      layout: { padding: { right: 44 } },
      scales: {
        x: { beginAtZero: true, grid: { color: GRID, drawTicks: false }, border: { display: false }, ticks: { color: INK3, padding: 6, maxRotation: 0, autoSkipPadding: 12 } },
        y: { grid: { display: false }, border: { color: GRID }, ticks: { color: INK2 } }
      },
      plugins: { legend: { display: false } }
    };
    return Object.assign(o, extra || {});
  }
  function single(id, labels, values, fmt, tip) {
    var el = document.getElementById(id);
    if (!el) return;
    charts.push(new Chart(el, {
      type: 'bar',
      data: { labels: labels, datasets: [{ data: values, backgroundColor: SINGLE, hoverBackgroundColor: '#c2540a',
        borderRadius: { topRight: 6, bottomRight: 6 }, borderSkipped: 'start', maxBarThickness: 20 }] },
      options: hbarOpts({ plugins: { legend: { display: false }, endLabels: { enabled: true, fmt: fmt },
        tooltip: { callbacks: { label: function (c) { return tip(c.raw, c.dataIndex); } } } } })
    }));
  }

  function draw() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var node = document.getElementById('dash-data');
    if (!node) return;
    var d = JSON.parse(node.textContent);

    // 1) 부서별 야근 시간과 원인 (쌓은 가로 막대)
    var ot = d.overtime, el = document.getElementById('ch-overtime');
    if (el) {
      var sets = CAUSE_ORDER.filter(function (c) { return ot.categories.indexOf(c) >= 0; }).map(function (c, k, arr) {
        var idx = ot.categories.indexOf(c);
        var last = k === arr.length - 1;
        return {
          label: c, data: ot.depts.map(function (dp) { return ot.hours[dp][idx]; }),
          backgroundColor: CAUSE_COLOR[c], borderColor: SURFACE,
          borderWidth: { right: last ? 0 : 2, left: 0, top: 0, bottom: 0 }, borderSkipped: false,
          borderRadius: last ? { topRight: 4, bottomRight: 4 } : 0, maxBarThickness: 24
        };
      });
      charts.push(new Chart(el, {
        type: 'bar', data: { labels: ot.depts, datasets: sets },
        options: hbarOpts({
          layout: { padding: { right: 8 } },
          scales: {
            x: { stacked: true, beginAtZero: true, grid: { color: GRID, drawTicks: false }, border: { display: false },
              title: { display: true, text: '시간', color: INK3, align: 'end', font: { size: 11 } },
              ticks: { color: INK3, padding: 6, maxRotation: 0, autoSkipPadding: 12 } },
            y: { stacked: true, grid: { display: false }, border: { color: GRID }, ticks: { color: INK2 } }
          },
          plugins: {
            legend: { display: true, position: 'top', align: 'start', labels: { boxWidth: 10, boxHeight: 10, color: INK2, padding: 12 } },
            tooltip: { mode: 'index', callbacks: {
              label: function (c) { return ' ' + c.dataset.label + ': ' + c.raw + '시간'; },
              footer: function (items) {
                var dp = ot.depts[items[0].dataIndex];
                return '전결 처리 비중 ' + Math.round(ot.jeongyeol_share[dp] * 100) + '%';
              } } }
          }
        })
      }));
    }

    // 2) 가장 하기 싫은 업무
    var sv = d.survey;
    single('ch-survey', sv.ranking.map(function (r) { return r.option; }), sv.ranking.map(function (r) { return r.score; }),
      function (v) { return v + '점'; },
      function (v, i) { return ' ' + v + '점 · 1순위로 꼽은 사람 ' + sv.ranking[i].first + '명'; });

    // 3) 반려 사유
    var rj = d.rejection;
    single('ch-reject', rj.reasons.map(function (r) { return r.label; }), rj.reasons.map(function (r) { return r.count; }),
      function (v) { return v + '건'; },
      function (v) { return ' ' + v + '건 (반려 ' + rj.rejected + '건 중 ' + Math.round(v / rj.rejected * 100) + '%)'; });

    // 4) 단계별 예상 절감 (가정)
    var sa = d.savings;
    single('ch-savings', sa.by_stage.map(function (r) { return r.stage; }), sa.by_stage.map(function (r) { return r.minutes_day_saved; }),
      function (v) { return v.toFixed(1) + '분'; },
      function (v, i) { var r = sa.by_stage[i]; return ' 1인 하루 ' + v.toFixed(1) + '분 (주 ' + r.hours_week_avg + '시간 × 가정 ' + Math.round(r.rate * 100) + '%)'; });
  }

  draw();
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === 'live') draw();
  });
})();
