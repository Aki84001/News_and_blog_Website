from flask import Flask, render_template, request, redirect, abort
import requests
import feedparser
import json, os
import markdown
from datetime import datetime, timedelta
from urllib.parse import urlparse
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
    query = f"(科学 OR 監督 インタビュー OR 歴史 OR 地学 OR 鉱物 OR 小説 SF OR 特別展) after:{after_date}"

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
        "q": "アニメ インタビュー" ,
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
RSS_FEEDS = [
    "https://nazology.kusuguru.co.jp/feed",#ナゾロジー
    "https://www.jstage.jst.go.jp/AF02S010Download?cdRss=003&rssLang=ja" #JSTAGE
]

 
def get_rss_articles():
    articles = []
    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        feed_title = getattr(feed.feed, "title", "RSS")
        for entry in feed.entries:
            # Atom形式はリンクが dict になってる場合がある
            link = entry.get("link", None)
            if isinstance(link, list):
                link = link[0].get("href", "#")

            # JSTAGEのRSSの場合は処理を分岐。形式にバラツキがあり、また数も多いため
            if "jstage.jst.go.jp" in feed_url:
                # タイトルは <author><name> から取得 
                author_name = entry.get("author", None)
                if not author_name:
                    # authorタグが無ければ無題
                    title = "無題"
                else:
                    title = author_name

                # published が存在する場合のみ日付チェック
                published_str = getattr(entry, "published", "")
                if published_str:
                    published_date = datetime.strptime(published_str[:10], "%Y-%m-%d")
                    if datetime.now() - published_date > timedelta(days=1):
                        continue  # 当日以外の記事はスキップ
                else:
                    published_date = None
            else:
                # Nazologyなどは従来通り
                title = getattr(entry, "title", "無題")
                published_date = getattr(entry, "published", "")

            articles.append({
                "title": title,
                "link": link or "#",
                "source": feed_title,
                "published": published_date
            })

    return articles




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
