// 목업 전결 웹 화면 공통 스크립트
(function () {
  // 3초 자동 갱신을 잠시 멈출 때: 입력 중이거나, 펼친 내용이 있거나, 탭이 가려져 있을 때
  window.liveOk = function () {
    if (document.hidden) return false;
    var live = document.getElementById('live');
    if (!live) return false;
    var a = document.activeElement;
    if (a && live.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return false;
    if (live.querySelector('details[open]')) return false;
    return true;
  };

  // 결재 결정 확인. 반려는 의견이 있어야 한다.
  window.confirmDecision = function (ev) {
    var btn = ev.submitter;
    var form = ev.target;
    var comment = (form.querySelector('[name=comment]') || {}).value || '';
    if (btn && btn.value === 'reject') {
      if (!comment.trim()) {
        alert('반려 사유를 의견 칸에 적어 주세요.');
        form.querySelector('[name=comment]').focus();
        return false;
      }
      return confirm('이 문서를 반려할까요?\n사유: ' + comment);
    }
    return confirm('이 문서를 승인할까요?');
  };
})();
