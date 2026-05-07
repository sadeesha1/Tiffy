"""
tools.py
--------
External API tool functions for Tiff.
All functions are synchronous and return a plain string — ready to feed
back to Claude as a tool_result.

Tools:
  get_weather        — current weather + forecast (Open-Meteo, free/no key)
  get_time           — current time in any timezone (WorldTimeAPI, free/no key)
  search_web         — quick web answer (DuckDuckGo Instant Answer, free/no key)
  search_wikipedia   — Wikipedia article summary (free/no key)
  get_news           — latest news headlines (GNews, free key)
  get_movie          — movie/TV info (OMDB/IMDb data, free key)
  get_book           — book info (Open Library, free/no key)
  get_definition     — word definition (Free Dictionary API, free/no key)
  get_holidays       — Sri Lanka public holidays (Nager.Date, free/no key)
  get_quote          — random inspirational quote (Quotable.io, free/no key)
  get_exchange_rate  — currency exchange rates (Frankfurter.app, free/no key)
"""

import httpx
from datetime import datetime
from urllib.parse import quote
from config import GNEWS_API_KEY, OMDB_API_KEY

TIMEOUT = 8  # seconds


def _get(url: str, **params) -> dict | None:
    """Simple GET with timeout. Returns parsed JSON or None on error."""
    try:
        r = httpx.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Weather ───────────────────────────────────────────────────────────────────

def get_weather(location: str) -> str:
    """Current weather + today's forecast for any location."""
    # Step 1: geocode
    geo = _get(
        "https://geocoding-api.open-meteo.com/v1/search",
        name=location, count=1, language="en", format="json"
    )
    if not geo or not geo.get("results"):
        return f"Couldn't find location: {location}"

    place = geo["results"][0]
    lat, lon = place["latitude"], place["longitude"]
    name = place.get("name", location)
    country = place.get("country", "")

    # Step 2: weather
    data = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        current="temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        daily="temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
        timezone="auto", forecast_days=3
    )
    if not data:
        return "Couldn't fetch weather data right now."

    c = data["current"]
    d = data["daily"]

    wmo = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Foggy", 51: "Light drizzle", 61: "Light rain", 63: "Moderate rain",
        65: "Heavy rain", 71: "Light snow", 80: "Rain showers", 95: "Thunderstorm"
    }
    condition = wmo.get(c.get("weather_code", 0), "Unknown")

    lines = [
        f"Weather in {name}, {country}:",
        f"Now: {c['temperature_2m']}°C (feels like {c['apparent_temperature']}°C), {condition}",
        f"Humidity: {c['relative_humidity_2m']}%  Wind: {c['wind_speed_10m']} km/h",
        "",
        "3-day forecast:"
    ]
    for i in range(min(3, len(d["time"]))):
        day_cond = wmo.get(d["weather_code"][i], "")
        lines.append(
            f"  {d['time'][i]}: {d['temperature_2m_min'][i]}–{d['temperature_2m_max'][i]}°C, "
            f"rain {d['precipitation_sum'][i]}mm, {day_cond}"
        )
    return "\n".join(lines)


# ── Time ──────────────────────────────────────────────────────────────────────

def get_time(timezone: str = "Asia/Colombo") -> str:
    """Current date and time in a given timezone. Uses local system + zoneinfo — no external call needed."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        return f"Current time in {timezone}: {now.strftime('%A, %B %d %Y — %I:%M:%S %p')}"
    except Exception:
        # Fallback to WorldTimeAPI if timezone key is unknown
        data = _get(f"https://worldtimeapi.org/api/timezone/{timezone}")
        if not data:
            return f"Couldn't get time for timezone: {timezone}"
        dt = data.get("datetime", "")[:19].replace("T", " ")
        return f"Current time in {timezone}: {dt}"


# ── Web search ────────────────────────────────────────────────────────────────

def search_web(query: str) -> str:
    """Quick web answer using DuckDuckGo Instant Answer."""
    data = _get("https://api.duckduckgo.com/", q=query, format="json", no_redirect=1, no_html=1)
    if not data:
        return "Couldn't reach search right now."

    parts = []
    if data.get("AbstractText"):
        parts.append(data["AbstractText"])
    if data.get("Answer"):
        parts.append(f"Answer: {data['Answer']}")
    for topic in data.get("RelatedTopics", [])[:3]:
        if isinstance(topic, dict) and topic.get("Text"):
            parts.append(f"• {topic['Text']}")

    if not parts:
        return f"No instant answer found for '{query}'. Try search_wikipedia for factual questions."
    return "\n".join(parts)


# ── Wikipedia ─────────────────────────────────────────────────────────────────

def search_wikipedia(query: str) -> str:
    """Search Wikipedia and return the article summary."""
    # Search for the best matching article
    search = _get(
        "https://en.wikipedia.org/w/api.php",
        action="query", list="search", srsearch=query, srlimit=1, format="json"
    )
    if not search or not search.get("query", {}).get("search"):
        return f"No Wikipedia article found for '{query}'."

    title = search["query"]["search"][0]["title"]

    # Get article summary
    summary = _get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
    if not summary:
        return f"Found article '{title}' but couldn't fetch its summary."

    extract = summary.get("extract", "No summary available.")
    if len(extract) > 800:
        extract = extract[:800] + "…"
    return f"Wikipedia — {title}:\n{extract}"


# ── News ──────────────────────────────────────────────────────────────────────

def get_news(topic: str, country: str = "lk") -> str:
    """Latest news headlines on a topic (GNews). Defaults to Sri Lanka news."""
    if not GNEWS_API_KEY:
        return "GNews API key not configured."
    data = _get(
        "https://gnews.io/api/v4/search",
        q=topic, token=GNEWS_API_KEY, lang="en", country=country, max=5, sortby="publishedAt"
    )
    # If no results for LK, retry without country filter
    if (not data or not data.get("articles")) and country != "":
        data = _get(
            "https://gnews.io/api/v4/search",
            q=topic, token=GNEWS_API_KEY, lang="en", max=5, sortby="publishedAt"
        )
    if not data or not data.get("articles"):
        return f"No news found for '{topic}'."

    lines = [f"Latest news on '{topic}':"]
    for a in data["articles"][:5]:
        pub = (a.get("publishedAt") or "")[:10]
        lines.append(f"\n• [{pub}] {a['title']}")
        if a.get("description"):
            desc = a["description"][:120]
            lines.append(f"  {desc}{'…' if len(a['description']) > 120 else ''}")
    return "\n".join(lines)


# ── Movie / TV ────────────────────────────────────────────────────────────────

def get_movie(title: str) -> str:
    """Movie or TV show info from OMDB (IMDb data)."""
    if not OMDB_API_KEY:
        return "OMDB API key not configured."
    data = _get("http://www.omdbapi.com/", t=title, apikey=OMDB_API_KEY, plot="short")
    if not data or data.get("Response") == "False":
        return f"Movie/show '{title}' not found."

    lines = [
        f"{data.get('Title')} ({data.get('Year')}) — {data.get('Type', '').capitalize()}",
        f"Genre: {data.get('Genre')}",
        f"Director: {data.get('Director')}",
        f"Cast: {data.get('Actors')}",
        f"IMDb: {data.get('imdbRating')}/10 ({data.get('imdbVotes')} votes)",
        f"Runtime: {data.get('Runtime')}",
        f"\n{data.get('Plot')}",
    ]
    if data.get("Awards") and data["Awards"] != "N/A":
        lines.append(f"Awards: {data['Awards']}")
    return "\n".join(lines)


# ── Books ─────────────────────────────────────────────────────────────────────

def get_book(title: str) -> str:
    """Book info from Open Library."""
    data = _get("https://openlibrary.org/search.json", title=title, limit=1, fields="title,author_name,first_publish_year,number_of_pages_median,subject")
    if not data or not data.get("docs"):
        return f"Book '{title}' not found."

    b = data["docs"][0]
    authors = ", ".join(b.get("author_name", ["Unknown"]))
    year = b.get("first_publish_year", "?")
    pages = b.get("number_of_pages_median", "?")
    subjects = ", ".join(b.get("subject", [])[:5]) or "N/A"

    return (
        f"{b.get('title')} by {authors}\n"
        f"First published: {year}  |  Pages: {pages}\n"
        f"Subjects: {subjects}"
    )


# ── Dictionary ────────────────────────────────────────────────────────────────

def get_definition(word: str) -> str:
    """English word definition, phonetics and examples."""
    data = _get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word.strip())}")
    if not data or not isinstance(data, list):
        return f"No definition found for '{word}'."

    entry = data[0]
    phonetic = entry.get("phonetic", "")
    lines = [f"{entry.get('word')} {phonetic}"]

    for meaning in entry.get("meanings", [])[:2]:
        lines.append(f"\n{meaning['partOfSpeech'].upper()}")
        for defn in meaning.get("definitions", [])[:2]:
            lines.append(f"  • {defn['definition']}")
            if defn.get("example"):
                lines.append(f"    e.g. \"{defn['example']}\"")
    return "\n".join(lines)


# ── Sri Lanka Holidays ────────────────────────────────────────────────────────

def get_holidays(year: int = 0) -> str:
    """Sri Lanka public holidays for a given year (defaults to current year)."""
    if not year:
        year = datetime.now().year
    data = _get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/LK")
    if not data:
        return f"Couldn't fetch Sri Lanka holidays for {year}."

    lines = [f"Sri Lanka public holidays {year}:"]
    for h in data:
        lines.append(f"  {h['date']} — {h['localName']} ({h['name']})")
    return "\n".join(lines)


# ── Quote ─────────────────────────────────────────────────────────────────────

def get_quote() -> str:
    """A random inspirational quote."""
    data = _get("https://api.quotable.io/random")
    if not data:
        # Fallback to quoteslate
        data = _get("https://quoteslate.vercel.app/api/quotes/random")
    if not data:
        return "Couldn't fetch a quote right now."
    content = data.get("content") or data.get("quote", "")
    author  = data.get("author") or "Unknown"
    return f'"{content}"\n— {author}'


# ── Exchange rates ────────────────────────────────────────────────────────────

def get_exchange_rate(from_currency: str, to_currency: str) -> str:
    """Live exchange rate between two currencies (e.g. USD to LKR)."""
    from_c = from_currency.upper().strip()
    to_c   = to_currency.upper().strip()
    # open.er-api.com covers 170+ currencies including LKR — no key needed
    data = _get(f"https://open.er-api.com/v6/latest/{from_c}")
    if not data or data.get("result") != "success":
        return f"Couldn't fetch exchange rate for {from_c} → {to_c}."
    rate = data.get("rates", {}).get(to_c)
    if rate is None:
        return f"Currency '{to_c}' not found."
    date = data.get("time_last_update_utc", "")[:16]
    return f"1 {from_c} = {rate} {to_c}  (as of {date})"
