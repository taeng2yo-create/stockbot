import os
import re
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
# 뉴스 수집
# ------------------------------------------------------------
def _fetch_rss_headlines(query, max_items=6):
    """구글 뉴스 RSS에서 헤드라인만 뽑아온다"""
    rss_url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=ko&gl=KR&ceid=KR:ko"
    )
    feed = feedparser.parse(rss_url)
    headlines = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "").strip()
        if title:
            headlines.append(f"- {title}")
    return headlines


def fetch_latest_news():
    """미국 증시 + 한국 증시 관련 최신 헤드라인 수집"""
    us_headlines = _fetch_rss_headlines("미국증시 나스닥 다우존스 S&P500", max_items=6)
    kr_headlines = _fetch_rss_headlines("한국증시 코스피 코스닥", max_items=6)

    news_text = "[미국 증시 관련 뉴스]\n" + (
        "\n".join(us_headlines) if us_headlines else "- (수집된 뉴스 없음)"
    )
    news_text += "\n\n[한국 증시 관련 뉴스]\n" + (
        "\n".join(kr_headlines) if kr_headlines else "- (수집된 뉴스 없음)"
    )
    return news_text


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
# 텔레그램 전송
# ------------------------------------------------------------
def _markdown_bold_to_html(text):
    """Gemini가 만든 **굵게** 표기를 텔레그램 HTML의 <b> 태그로 변환하고,
    나머지 텍스트는 HTML 특수문자를 이스케이프한다."""

    # 1) 먼저 **...** 조각들을 분리해둔다
    parts = re.split(r"(\*\*.*?\*\*)", text, flags=re.DOTALL)

    escaped_parts = []
    for part in parts:
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            inner = part[2:-2]
            inner = (
                inner.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            escaped_parts.append(f"<b>{inner}</b>")
        else:
            part = (
                part.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            escaped_parts.append(part)

    return "".join(escaped_parts)


def send_telegram_message(text):
    """텔레그램으로 메시지 전송 (HTML 파싱 모드 사용 - Markdown보다 훨씬 안정적)"""
    html_text = _markdown_bold_to_html(text)

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
            resp_body = resp.read().decode("utf-8")
            print("텔레그램 전송 성공:", resp_body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print("텔레그램 전송 실패! 응답 내용:", error_body)

        # HTML 파싱마저 실패하면, 서식 없이 순수 텍스트로 최후 재시도
        print("서식 없는 일반 텍스트로 재시도합니다...")
        fallback_payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": re.sub(r"\*\*", "", text),  # ** 만 제거한 원본 텍스트
        }
        fallback_data = urllib.parse.urlencode(fallback_payload).encode("utf-8")
        fallback_req = urllib.request.Request(url, data=fallback_data)
        with urllib.request.urlopen(fallback_req) as resp2:
            print("일반 텍스트 재전송 성공:", resp2.read().decode("utf-8"))


# ------------------------------------------------------------
# 메인 실행
# ------------------------------------------------------------
if __name__ == "__main__":
    print("환경변수 확인 중...")
    check_env()

    print("뉴스 수집 중...")
    news = fetch_latest_news()
    print(news)

    print("Gemini로 브리핑 생성 중...")
    briefing = generate_briefing(news)
    print("생성된 브리핑:\n", briefing)

    print("텔레그램 전송 중...")
    send_telegram_message(briefing)

    print("완료!")
