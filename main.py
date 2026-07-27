import os
import urllib.parse
import urllib.request
import feedparser
from google import genai

# 환경변수 가져오기
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def fetch_latest_news():
  """구글 뉴스 RSS를 통해 미 증시 및 한국 증시 관련 핵심 뉴스 수집"""
  rss_url = "https://news.google.com/rss/search?q=%EB%AF%B8%EA%B5%AD%EC%A6%9D%EC%8B%9C+%ED%95%9C%EA%B5%AD%EC%A6%9D%EC%8B%9C&hl=ko&gl=KR&ceid=KR:ko"
  feed = feedparser.parse(rss_url)
  headlines = []
  for entry in feed.entries[:8]:  # 상위 8개 기사 헤드라인 수집
    headlines.append(f"- {entry.title}")
  return "\n".join(headlines)


def generate_briefing(news_data):
  """Gemini API를 활용해 모닝 브리핑 원고 생성"""
  if not API_KEY:
    raise ValueError(
        "API 키가 설정되지 않았습니다. GitHub Secrets의 GEMINI_API_KEY를"
        " 확인해 주세요."
    )

  client = genai.Client(api_key=API_KEY)

  prompt = f"""
    너는 최고 수준의 수석 증권 분석가야.
    아래 수집된 최신 뉴스와 시장 동향을 바탕으로, 한국시간 기준 오늘 아침 브리핑을 작성해줘.

    [오늘 아침 주요 뉴스 데이터]
    {news_data}

    [작성 양식]
    1. 📊 **밤사이 미국 증시 요약** (주요 지수 동향 및 핵심 이슈 2~3가지)
    2. 🇰🇷 **오늘 한국 증시 관전 포인트** (주요 영향 요소 및 주목할 섹터)
    3. 💡 **오늘의 투자 체크포인트** (간단한 한 줄 조언)

    - 텔레그램으로 읽기 쉽고 명확하며 모바일 보기 좋게 가독성을 맞춰서 정리해줘.
    - 너무 길지 않게 핵심 위주로 작성해줘.
    """

  response = client.models.generate_content(
      model="gemini-2.5-flash",
      contents=prompt,
  )
  return response.text


def send_telegram_message(text):
  """텔레그램으로 메세지 전송"""
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  data = urllib.parse.urlencode(
      {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
  ).encode("utf-8")
  req = urllib.request.Request(url, data=data)
  urllib.request.urlopen(req)


if __name__ == "__main__":
  news = fetch_latest_news()
  briefing = generate_briefing(news)
  send_telegram_message(briefing)
