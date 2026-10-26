# 목업 전결 시스템 배포용 이미지 (웹 화면 + REST API + MCP를 한 포트에서).
#   docker build -t approval-demo .
#   docker run -p 8000:8000 -v approval-data:/app/data \
#     -e APPROVAL_BASE_URL=https://<공개 주소> -e APPROVAL_ADMIN_KEY=<운영자 키> -e APPROVAL_MCP_SECRET=<임의 문자열> approval-demo
# HTTPS는 앞단(로드 밸런서·리버스 프록시·클라우드 플랫폼)에서 붙인다. 모든 데이터는 시연용 더미다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    APPROVAL_HOME=/app \
    APPROVAL_HOST=0.0.0.0 \
    APPROVAL_PORT=8000 \
    APPROVAL_MCP_MOUNT=1 \
    APPROVAL_CLOCK_FLOW=frozen

RUN pip install --no-cache-dir "uv>=0.8"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY core ./core
COPY web ./web
COPY mcp_server ./mcp_server
COPY seedgen ./seedgen
COPY seed ./seed
COPY demo_inputs ./demo_inputs
RUN uv sync --frozen --no-dev

EXPOSE 8000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT') or os.environ.get('APPROVAL_PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=4).status == 200 else 1)"
CMD ["uv", "run", "--no-sync", "approval-server", "deploy"]
