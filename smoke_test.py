"""Smoke test — run from the Tiff directory: python smoke_test.py"""
import sys, time
sys.path.insert(0, ".")

results = []

def check(name, fn):
    t0 = time.time()
    try:
        result = fn()
        ms = int((time.time() - t0) * 1000)
        results.append(("PASS", name, str(result)[:100], ms))
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        results.append(("FAIL", name, str(e)[:100], ms))

# ── Config ────────────────────────────────────────────────────────────────────
check("config loads",           lambda: __import__("config"))
check("config.MODEL set",       lambda: __import__("config").MODEL)
check("config.GNEWS_API_KEY",   lambda: bool(__import__("config").GNEWS_API_KEY))
check("config.OMDB_API_KEY",    lambda: bool(__import__("config").OMDB_API_KEY))

# ── Memory ────────────────────────────────────────────────────────────────────
check("memory imports",         lambda: __import__("memory"))
check("memory.init_db()",       lambda: __import__("memory").init_db())
check("memory.get_all_facts()", lambda: __import__("memory").get_all_facts())
check("memory.get_history()",   lambda: __import__("memory").get_history(limit=5))
check("memory.get_open_threads()", lambda: __import__("memory").get_open_threads())
check("memory.remember()",      lambda: __import__("memory").remember("smoke test fact — delete me"))
check("memory.recall(smoke)",   lambda: __import__("memory").recall("smoke test"))

# ── Tools: imports ────────────────────────────────────────────────────────────
check("tools imports",          lambda: __import__("tools"))

from tools import (
    get_weather, get_time, search_wikipedia, get_exchange_rate,
    get_holidays, get_definition, get_quote, search_web,
    get_book, get_movie, get_news,
)

# ── Tools: time (local zoneinfo — no network) ─────────────────────────────────
check("get_time(Colombo)",      lambda: get_time("Asia/Colombo"))
check("get_time(London)",       lambda: get_time("Europe/London"))
check("get_time(New York)",     lambda: get_time("America/New_York"))

# ── Tools: no-key network APIs ────────────────────────────────────────────────
check("get_weather(Colombo)",           lambda: get_weather("Colombo"))
check("get_weather(Kandy)",             lambda: get_weather("Kandy"))
check("get_exchange_rate(USD→LKR)",     lambda: get_exchange_rate("USD", "LKR"))
check("get_exchange_rate(EUR→LKR)",     lambda: get_exchange_rate("EUR", "LKR"))
check("search_wikipedia(Sri Lanka)",    lambda: search_wikipedia("Sri Lanka"))
check("search_wikipedia(C# — special chars)", lambda: search_wikipedia("C# programming language"))
check("get_holidays(2026)",             lambda: get_holidays(2026))
check("get_definition(ephemeral)",      lambda: get_definition("ephemeral"))
check("get_definition(look up — spaces)", lambda: get_definition("look"))
check("get_quote()",                    lambda: get_quote())
check("search_web(IPL 2026)",          lambda: search_web("IPL 2026"))
check("get_book(Dune)",                lambda: get_book("Dune"))

# ── Tools: keyed APIs ────────────────────────────────────────────────────────
check("get_news(Sri Lanka)",            lambda: get_news("Sri Lanka"))
check("get_news(cricket)",              lambda: get_news("cricket"))
check("get_movie(Interstellar)",        lambda: get_movie("Interstellar"))

# ── Brain ─────────────────────────────────────────────────────────────────────
check("brain imports",                  lambda: __import__("brain"))
check("brain.build_system() — 2 blocks", lambda: len(__import__("brain").build_system()) == 2)
check("brain.TOOLS — 16 tools",         lambda: len(__import__("brain").TOOLS) == 16)
check("brain has asyncio.to_thread",    lambda: "asyncio.to_thread" in open("brain.py", encoding="utf-8").read())

# ── Print results ─────────────────────────────────────────────────────────────
passed = [r for r in results if r[0] == "PASS"]
failed = [r for r in results if r[0] == "FAIL"]

print(f"\n{'-'*60}")
print(f"  Tiff Smoke Test")
print(f"{'-'*60}")
for status, name, detail, ms in results:
    icon = "+" if status == "PASS" else "X"
    timing = f"{ms}ms"
    print(f"  {icon}  {name:<42} {timing:>6}")
    if status == "FAIL":
        print(f"     ERROR: {detail}")
    elif detail and detail not in ("None", "[]", "{}", "True", "False", "None"):
        snippet = detail[:80].replace("\n", " ")
        print(f"     {snippet}")
print(f"{'-'*60}")
print(f"  {len(passed)} passed  |  {len(failed)} failed")
print(f"{'─'*60}\n")

sys.exit(0 if not failed else 1)
