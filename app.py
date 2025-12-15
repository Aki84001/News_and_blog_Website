from flask import Flask, render_template, request, redirect
import requests
import feedparser
import json, os
import markdown
from datetime import datetime, timedelta
import boto3
from dotenv import load_dotenv
load_dotenv()
import os


s3 = boto3.client("s3")
app = Flask(__name__)

#
after_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")


SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
SERPER_API_URL = os.getenv("SERPER_API_URL")
BRAVE_NEWS_URL = os.getenv("BRAVE_NEWS_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_BLOG_FILE = "blog_posts.json"
S3_REGION = "ap-northeast-1"



# ====== APIの取得結果フォーマットの統合 ======
def normalize_serper(item):
    return {
        "title": item.get("title"),
        "link": item.get("link"),
        "source": item.get("source") or item.get("newsSource") or "Serper",
    }

def normalize_brave(item):
    return {
        "title": item.get("title", "無題"),
        "link": item.get("url", "#"),
         "source": item.get("meta_url", {}).get("hostname", "Brave"),
    }

#S3の画像置き場URL取得
def s3_image(filename):
    return f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com/{filename}"

app.jinja_env.globals["s3"] = s3_image


def save_blog_post(post):
    posts = load_blog_posts()
    posts.append(post)

    try:
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=S3_BLOG_FILE,
            Body=json.dumps(posts, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json"
        )
        print("Saved to S3 OK")

    except Exception as e:
        print("S3 save error:", e)

def load_blog_posts():

    try:
        obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=S3_BLOG_FILE)
        data = obj["Body"].read().decode("utf-8")
        return json.loads(data)
    except s3.exceptions.NoSuchKey:
        # ファイルが無い場合は空リスト
        return []

    except Exception as e:
        print("S3 load error:", e)
        return []

def get_serper_news():
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json"
    }

    # 今日から3日前の日付を生成
    after_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    # クエリ文字列を組み立てる
    query = f"(企画展 OR 特別展) after:{after_date}"

    payload = {"q": query, "num": 20}  # num=取得件数

    try:
        response = requests.post(SERPER_API_URL, headers=headers, json=payload)
        print(f"Serper.dev ステータスコード: {response.status_code}")
        data = response.json()
        items = data.get("news", data.get("organic", []))

        # MSN経由での重複記事表示を避けつつ normalize する
        normalized = [
            normalize_serper(item)
            for item in items
            if (item.get("source") or item.get("newsSource")) != "MSN"
        ]

        return normalized

    except Exception as e:
        print("Serper.devエラー:", e)
        return []

def get_brave_news():
    headers = {
        "X-Subscription-Token": BRAVE_API_KEY,
        "Accept": "application/json"
    }

    params = {
        "q": "アニメ インタビュー SF" ,
        "count": 10,
        "country": "jp",
        "freshness": "3d"
    }

    try:
        response = requests.get(BRAVE_NEWS_URL, headers=headers, params=params)
        print("Brave ステータス:", response.status_code)
    

        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))

        articles = data.get("results", [])

        return [
            normalize_brave(item)
            for item in articles
            if item.get("publisher") != "msn.com"
        ]

    except Exception as e:
        print("Braveエラー:", e)
        return []


def delduplicate_articles(articles):
    seen = set()
    unique_articles = []
    for a in articles:
        key = a.get("link") or a.get("title")
        if key not in seen:
            seen.add(key)
            unique_articles.append(a)
    return unique_articles





# ====== RSSの設定 ======
RSS_SITES = {
    "ナゾロジー": {
        "url": "https://nazology.kusuguru.co.jp/feed",
        "description": "科学ニュースや不思議なトピックを分かりやすく紹介するライトな科学メディアです！  一番良く見てます",
        "limit": 10
    },
    "Nature Asia": {
        "url": "https://www.natureasia.com/ja-jp/rss",
        "description": "Nature の日本語圏向けニュースと研究紹介。",
        "limit": 10
    },
    "Science Portal": {
        "url": "https://scienceportal.jst.go.jp/feed/rss.xml",
        "description": "科学技術振興機構による科学ニュース全般。",
        "limit": 10
    },
    "JSTAGE": {
        "url": "https://www.jstage.jst.go.jp/AF02S010Download?cdRss=003&rssLang=ja",
        "description": "JSTAGEで直近に公開された学会誌の一覧です。当日分だけ出してますがあまりに数が多いので気が向いたやつだけ見てください・・・。",
        "limit": 20
    }
}

def get_rss_articles():
    results = []

    for site_name, info in RSS_SITES.items():
        feed = feedparser.parse(info["url"])
        site_articles = []

        for entry in feed.entries:
            # link の形式対策
            link = entry.get("link")
            if isinstance(link, list):
                link = link[0].get("href", "#")

            # ===== JSTAGE 専用処理 =====
            if "jstage.jst.go.jp" in info["url"]:
                title = entry.get("author", "無題")

                published_str = getattr(entry, "published", "")
                if not published_str:
                    continue

                try:
                    published_date = datetime.strptime(
                        published_str[:10], "%Y-%m-%d"
                    )
                except ValueError:
                    continue

                if datetime.now() - published_date > timedelta(days=1):
                    continue

            # ===== 通常RSS =====
            else:
                title = getattr(entry, "title", "無題")

            site_articles.append({
                "title": title,
                "link": link or "#"
            })

            if len(site_articles) >= info["limit"]:
                break

        results.append({
            "site_name": site_name,
            "description": info["description"],
            "articles": site_articles
        })

    return results

 





# ====== Flaskルート ======
@app.route("/")
def index():
    serper_news = get_serper_news()
    brave_news = get_brave_news()
    rss_articles = get_rss_articles()
    blog_posts = load_blog_posts()
    posts_sorted = sorted(blog_posts, key=lambda x: x["id"], reverse=True)    
    all_news = delduplicate_articles(serper_news + brave_news)
    return render_template(
        "index.html",
        news=all_news,
        rss=rss_articles,
        blog=posts_sorted
    )

@app.route("/new", methods=["GET", "POST"])
def new_post():
    if request.method == "POST":
        posts = load_blog_posts()
        next_id = (max([p["id"] for p in posts]) + 1) if posts else 1

        post = {
            "id": next_id,
            "title": request.form["title"],
            "thumbnail": request.form.get("thumbnail", ""),
            "tags": [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()],
            "images": [i.strip() for i in request.form.get("images", "").split(",") if i.strip()],
            "content": request.form["content"],
            "created_at": datetime.now().isoformat()
        }

        save_blog_post(post)
        return redirect(f"/post/{next_id}")
    return render_template("new_post.html")


@app.route("/post/<int:post_id>")
def show_post(post_id):
    posts = load_blog_posts()
    target = next((p for p in posts if p["id"] == post_id), None)
    if not target:
        return "Not Found", 404

    # Markdown → HTML
    html_body = markdown.markdown(
        target["content"],
        extensions=["extra", "nl2br"]
    )

    return render_template("post.html", post=target, html_content=html_body)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
