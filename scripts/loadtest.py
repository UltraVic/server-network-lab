"""
Phase 5 부하 테스트 — 동시에 N개의 '느린' 요청을 던져 전체 시간을 잰다.
각 요청은 /api/work?ms=100 → 백엔드가 DB 커넥션을 빌려 100ms 잠든다.

  풀=1  → 한 번에 1개만 → 직렬화 → 전체 ≈ N * 100ms
  풀=10 → 한 번에 10개씩 → 전체 ≈ (N/10) * 100ms

사용:
  python scripts/loadtest.py [동시요청수=50] [작업ms=100]
"""
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50
MS = int(sys.argv[2]) if len(sys.argv) > 2 else 100
URL = f"http://localhost:8080/api/work?ms={MS}"


def one(_):
    t = time.perf_counter()
    with urllib.request.urlopen(URL, timeout=120) as r:
        r.read()
    return time.perf_counter() - t


start = time.perf_counter()
with ThreadPoolExecutor(max_workers=N) as ex:
    latencies = sorted(ex.map(one, range(N)))
total = time.perf_counter() - start

print(f"  동시 요청 {N}개 x 각 {MS}ms 작업")
print(f"  전체 소요(wall)  : {total:.2f}s")
print(f"  요청당 지연 중앙값: {latencies[len(latencies)//2]*1000:.0f}ms")
print(f"  최소 / 최대 지연 : {latencies[0]*1000:.0f}ms / {latencies[-1]*1000:.0f}ms")
print(f"  이상적 추정       : 풀=1 이면 ~{N*MS/1000:.1f}s, 풀=10 이면 ~{N*MS/1000/10:.1f}s")
