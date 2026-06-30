# 백엔드 컨테이너 — Phase 3(Docker Compose)에서 사용
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 복사 → 레이어 캐시 활용
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# 컨테이너 내부에서 8000 리슨.
# ※ 이 EXPOSE는 "문서화"일 뿐 호스트로 여는 게 아니다.
#   실제 호스트 노출은 docker-compose.yml 의 ports: 가 결정한다 (Phase 3 핵심).
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
