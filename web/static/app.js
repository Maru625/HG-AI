// 목업 전결 웹 화면 공통 스크립트
(function () {
  // 입력하다 만 양식이 3초 자동 갱신에 지워지지 않게, 고친 양식에 표시를 남긴다
  function markDirty(e) {
    var f = e.target && e.target.form;
    if (f) f.setAttribute('data-dirty', '1');
  }
  document.addEventListener('input', markDirty, true);
  document.addEventListener('change', markDirty, true);

  // 3초 자동 갱신을 잠시 멈출 때: 입력 중이거나, 고친 양식이 있거나, 펼친 내용이 있거나, 탭이 가려져 있을 때
  window.liveOk = function () {
    if (document.hidden) return false;
    var live = document.getElementById('live');
    if (!live) return false;
    var a = document.activeElement;
    if (a && live.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return false;
    if (live.querySelector('form[data-dirty]')) return false;
    if (live.querySelector('details[open]')) return false;
    return true;
  };

  // 복사 버튼 (AI 연결 화면): data-copy="#값을 담은 요소". https·localhost가 아니면 선택 후 execCommand로 복사한다.
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest && ev.target.closest('[data-copy]');
    if (!btn) return;
    var el = document.querySelector(btn.getAttribute('data-copy'));
    if (!el) return;
    var text = el.textContent.trim();
    var done = function (ok) {
      var old = btn.getAttribute('data-label') || btn.textContent;
      btn.setAttribute('data-label', old);
      btn.textContent = ok ? '복사됨' : '선택됨 · Ctrl+C';
      btn.classList.toggle('copied', ok);
      setTimeout(function () { btn.textContent = old; btn.classList.remove('copied'); }, 1600);
    };
    var fallback = function () {
      var r = document.createRange(); r.selectNodeContents(el);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      done(ok);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
    } else {
      fallback();
    }
  });

  // 결재 결정 확인. 반려(합의하지 않음)는 의견이 있어야 한다.
  window.confirmDecision = function (ev) {
    var btn = ev.submitter;
    var form = ev.target;
    var agree = form.getAttribute('data-kind') === '합의';
    var comment = (form.querySelector('[name=comment]') || {}).value || '';
    if (btn && btn.value === 'reject') {
      if (!comment.trim()) {
        alert(agree ? '합의하지 않는 사유를 의견 칸에 적어 주세요.' : '반려 사유를 의견 칸에 적어 주세요.');
        form.querySelector('[name=comment]').focus();
        return false;
      }
      return confirm((agree ? '합의하지 않고 반려할까요?' : '이 문서를 반려할까요?') + '\n사유: ' + comment);
    }
    return confirm(agree ? '이 문서에 합의할까요?' : '이 문서를 승인할까요?');
  };
})();
