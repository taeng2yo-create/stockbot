import os
import re
import html
import urllib.parse
import urllib.request
import urllib.error

import feedparser
from google import genai

# ------------------------------------------------------------
# 환경변수 (GitHub Secrets에서 주입됨)
# ------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def check_env():
    """필수 환경변수가 모두 설정되어 있는지 확인"""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not API_KEY:
        missing.append("GEMINI_API_KEY (또는 GOOGLE_API_KEY)")
    if missing:
        raise ValueError(
            "다음 환경변수(GitHub Secrets)가 설정되지 않았습니다: "
            + ", ".join(missing)
        )


# ------------------------------------------------------------
# 뉴스 수집 (제목 + 링크)
# ------------------------------------------------------------
def _fetch_rss_items(rss_url, max_items=6):
    """RSS 피드에서 (제목, 링크) 쌍을 뽑아온다"""
    feed = feedparser.parse(rss_url)
    items = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if title and link:
            items.append({"title": title, "link": link})
    return items


def _google_news_rss(query, max_items=6):
    rss_url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=ko&gl=KR&ceid=KR:ko"
    )
    return _fetch_rss_items(rss_url, max_items=max_items)


def _yahoo_finance_rss(max_items=6):
    # Yahoo Finance 주요 뉴스 RSS
    rss_url = "https://finance.yahoo.com/news/rssindex"
    return _fetch_rss_items(rss_url, max_items=max_items)


def fetch_latest_news():
    """미국 증시 + 한국 증시 관련 최신 뉴스(제목+링크) 수집"""
    us_google = _google_news_rss("미국증시 나스닥 다우존스 S&P500", max_items=4)
    us_yahoo = _yahoo_finance_rss(max_items=4)
    kr_google = _google_news_rss("한국증시 코스피 코스닥", max_items=6)

    # 중복 제목 제거
    def dedupe(items):
        seen = set()
        result = []
        for it in items:
            key = it["title"]
            if key not in seen:
                seen.add(key)
                result.append(it)
        return result

    us_items = dedupe(us_google + us_yahoo)
    kr_items = dedupe(kr_google)

    # Gemini 프롬프트용 텍스트 (제목만)
    news_text = "[미국 증시 관련 뉴스]\n" + (
        "\n".join(f"- {it['title']}" for it in us_items)
        if us_items
        else "- (수집된 뉴스 없음)"
    )
    news_text += "\n\n[한국 증시 관련 뉴스]\n" + (
        "\n".join(f"- {it['title']}" for it in kr_items)
        if kr_items
        else "- (수집된 뉴스 없음)"
    )

    return news_text, us_items, kr_items


# ------------------------------------------------------------
# Gemini로 브리핑 원고 생성
# ------------------------------------------------------------
def generate_briefing(news_data):
    """Gemini API를 활용해 모닝 브리핑 원고 생성"""
    client = genai.Client(api_key=API_KEY)

    prompt = f"""
너는 최고 수준의 수석 증권 분석가야.
아래 수집된 최신 뉴스와 시장 동향을 바탕으로, 한국시간 기준 오늘 아침 브리핑을 작성해줘.

[오늘 아침 주요 뉴스 데이터]
{news_data}

[작성 양식]
1. 📊 밤사이 미국 증시 요약 (주요 지수 동향 및 핵심 이슈 2~3가지)
2. 🇰🇷 오늘 한국 증시 관전 포인트 (주요 영향 요소 및 주목할 섹터)
3. 💡 오늘의 투자 체크포인트 (간단한 한 줄 조언)

- 텔레그램으로 읽기 쉽고 명확하며 모바일 보기 좋게 가독성을 맞춰서 정리해줘.
- 중요한 부분은 **단어** 형태로 강조해줘.
- 너무 길지 않게 핵심 위주로 작성해줘.
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )

    if not response or not getattr(response, "text", None):
        raise RuntimeError("Gemini가 빈 응답을 반환했습니다.")

    return response.text


# ------------------------------------------------------------
# 텔레그램 메시지 조립 (HTML)
# ------------------------------------------------------------
def _markdown_bold_to_html(text):
    """Gemini가 만든 **굵게** 표기를 텔레그램 HTML <b> 태그로 변환하고,
    나머지 텍스트는 HTML 특수문자를 이스케이프한다."""
    parts = re.split(r"(\*\*.*?\*\*)", text, flags=re.DOTALL)
    escaped_parts = []
    for part in parts:
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            inner = html.escape(part[2:-2], quote=False)
            escaped_parts.append(f"<b>{inner}</b>")
        else:
            escaped_parts.append(html.escape(part, quote=False))
    return "".join(escaped_parts)


def _build_links_section(title, items, max_links=4):
    """뉴스 항목들을 텔레그램 HTML 링크 목록으로 변환"""
    if not items:
        return ""
    lines = [f"\n\n🔗 <b>{html.escape(title, quote=False)}</b>"]
    for it in items[:max_links]:
        safe_title = html.escape(it["title"], quote=False)
        safe_link = html.escape(it["link"], quote=True)
        lines.append(f'• <a href="{safe_link}">{safe_title}</a>')
    return "\n".join(lines)


def build_final_message(briefing_text, us_items, kr_items):
    body_html = _markdown_bold_to_html(briefing_text)
    links_html = _build_links_section("미국 증시 관련 기사", us_items, max_links=4)
    links_html += _build_links_section("한국 증시 관련 기사", kr_items, max_links=4)
    return body_html + links_html


# ------------------------------------------------------------
# 텔레그램 전송
# ------------------------------------------------------------
def send_telegram_message(html_text, plain_fallback_text):
    """텔레그램으로 메시지 전송 (HTML 파싱 모드, 실패 시 순수 텍스트로 재시도)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(req) as resp:
            print("텔레그램 전송 성공:", resp.read().decode("utf-8"))
        return
    except urllib.error.HTTPError as e:
        print("텔레그램 전송 실패! 응답 내용:", e.read().decode("utf-8"))
        print("서식 없는 일반 텍스트로 재시도합니다...")

    fallback_payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": plain_fallback_text,
        "disable_web_page_preview": True,
    }
    fallback_data = urllib.parse.urlencode(fallback_payload).encode("utf-8")
    fallback_req = urllib.request.Request(url, data=fallback_data)
    with urllib.request.urlopen(fallback_req) as resp2:
        print("일반 텍스트 재전송 성공:", resp2.read().decode("utf-8"))


def _build_plain_fallback(briefing_text, us_items, kr_items):
    plain = re.sub(r"\*\*", "", briefing_text)
    if us_items:
        plain += "\n\n[미국 증시 관련 기사]\n" + "\n".join(
            f"- {it['title']}: {it['link']}" for it in us_items[:4]
        )
    if kr_items:
        plain += "\n\n[한국 증시 관련 기사]\n" + "\n".join(
            f"- {it['title']}: {it['link']}" for it in kr_items[:4]
        )
    return plain


# ------------------------------------------------------------
# 메인 실행
# ------------------------------------------------------------
if __name__ == "__main__":
    print("환경변수 확인 중...")
    check_env()

    print("뉴스 수집 중...")
    news_text, us_items, kr_items = fetch_latest_news()
    print(news_text)

    print("Gemini로 브리핑 생성 중...")
    briefing = generate_briefing(news_text)
    print("생성된 브리핑:\n", briefing)

    print("메시지 조립 중...")
    final_html = build_final_message(briefing, us_items, kr_items)
    fallback_text = _build_plain_fallback(briefing, us_items, kr_items)

    print("텔레그램 전송 중...")
    send_telegram_message(final_html, fallback_text)

    print("완료!")
