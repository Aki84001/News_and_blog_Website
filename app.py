from flask import Flask, render_template, request, redirect,session
import requests
import feedparser
import json, os
import markdown
from datetime import datetime, timedelta
import boto3
from dotenv import load_dotenv
load_dotenv()
import os
import logging




s3 = boto3.client("s3")
app = Flask(__name__)


after_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logging.info("Saved to S3 OK")
logging.error("S3 save error", exc_info=True)


#ニュースAPIのurlとシークレットキー
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
SERPER_API_URL = os.getenv("SERPER_API_URL")
BRAVE_NEWS_URL = os.getenv("BRAVE_NEWS_URL")

#AWSの資材置き場
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_BLOG_FILE = "blog_posts.json"
S3_REGION = "ap-northeast-1"

#管理者ログイン用パス
ADMIN_LOGIN_KEY = os.getenv("ADMIN_LOGIN_KEY")
app.secret_key = os.getenv("SECRET_KEY")


NEWS_CACHE = {
    "data": None,
    "updated": None
}


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

    # 3日以上経った記事は表示させないようにする。
    after_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    # クエリ文字列を組み立てる
    query = f"(企画展 OR 特別展 東京) after:{after_date}"

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


#get_brave_newsで呼び出す関数。記事一覧やまとめみたいなページまで持ってきてしまうので除外する
def is_article_url(url: str) -> bool:
    if not url:
        return False

    blacklist = [
        "/tag/",
        "/tags/",
        "/category/",
        "/categories/",
        "index.html",
        "details.php",
        "/list/",
        "?p=",
        "?page=",
        "/interview/index",
    ]

    return not any(bad in url for bad in blacklist)



#brave
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

        articles = data.get("results", [])

        return [
            normalize_brave(item)
            for item in articles
            if item.get("publisher") != "msn.com"
            and is_article_url(item.get("url", ""))
        ]

    except Exception as e:
        print("Braveエラー:", e)
        return []




#取得した記事は30分間ほど保持しておく
def get_cached_news():
    if NEWS_CACHE["updated"] and datetime.now() - NEWS_CACHE["updated"] < timedelta(minutes=30):
        return NEWS_CACHE["data"]

    data = {
        "academic": get_serper_news(),
        "subculture": get_brave_news(),
        "rss": get_rss_articles()
    }

    NEWS_CACHE["data"] = data
    NEWS_CACHE["updated"] = datetime.now()
    return data

def admin_required():
    if not session.get("admin"):
        return redirect("/admin/login")




# ====== RSSの設定 ======
RSS_SITES = {
    "ナゾロジー": {
        "url": "https://nazology.kusuguru.co.jp/feed",
        "description": "科学ニュースや不思議なトピックを分かりやすく紹介するライトな科学メディアです！  一番良く見てます",
        "limit": 10,
        "icon": "framingo.jpg"

    },
    "Nature Asia": {
        "url": "https://www.natureasia.com/ja-jp/rss/nature",
        "description": "Nature の日本語圏向けニュースと研究紹介のハイライトです。",
        "limit": 100,
        "icon": "hasibiro.jpg"
    },

    "JSTAGE": {
        "url": "https://www.jstage.jst.go.jp/AF02S010Download?cdRss=003&rssLang=ja",
        "description": "JSTAGEという日本の論文が集められたサイトで直近学会誌を出した団体の一覧です。ものによってはフリーじゃなかったりするんで見れないやつもあるかもです・・・。",
        "limit": 100,
        "icon": "dacyou.jpg"
    }
}

def get_rss_articles():
    results = []

    for site_name, info in RSS_SITES.items():
        feed = feedparser.parse(info["url"])
        site_articles = []

        # JSTAGE用の重複排除セット
        seen_titles = set()

        for entry in feed.entries:
            link = entry.get("link")
            if isinstance(link, list):
                link = link[0].get("href", "#")

            # ===== JSTAGE =====
            if "jstage.jst.go.jp" in info["url"]:
                title = entry.get("author", "無題")

                # 重複排除（ここが肝）
                if title in seen_titles:
                    continue
                seen_titles.add(title)

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

        # S3 の画像 URL をセット
        icon_url = s3_image(f"/images/{info['icon']}")

        results.append({
            "site_name": site_name,
            "description": info["description"],
            "articles": site_articles,
            "icon_url": icon_url
        })

    return results

def reflect_changeresult_of_blog_posts(posts):
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=S3_BLOG_FILE,
        Body=json.dumps(posts, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json"
    )





# ====== Flaskルート ======
@app.route("/")
def index():
    news = get_cached_news()
    rss_articles = get_rss_articles()
    blog_posts = load_blog_posts()
    posts_sorted = sorted(blog_posts, key=lambda x: x["id"], reverse=True)    
    return render_template(
        "index.html",
        academic_news=news["academic"],
        subculture_news=news["subculture"],
        rss=rss_articles,
        blog=posts_sorted
    )


#記事の投稿画面
@app.route("/admin/new", methods=["GET", "POST"])
def new_post():

    if not session.get("admin"):
        return redirect("/admin/login")

    if request.method == "POST":
        posts = load_blog_posts()
        next_id = (max([p["id"] for p in posts]) + 1) if posts else 1

        post = {
            "id": next_id,
            "title": request.form["title"],
            "subtitle": request.form.get("subtitle", ""),

            "thumbnail": request.form.get("thumbnail", ""),
            "tags": [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()],
            "images": [i.strip() for i in request.form.get("images", "").split(",") if i.strip()],
            "content": request.form["content"],
            "created_at": datetime.now().isoformat()
        }

        save_blog_post(post)
        return redirect(f"/post/{next_id}")
    return render_template("post_form.html")

#記事の表示画面
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

#記事の編集画面
@app.route("/admin/edit/<int:post_id>", methods=["GET", "POST"])
def edit_post(post_id):
    posts = load_blog_posts()

    if not session.get("admin"):
        return redirect("/admin/login")

    post = next((p for p in posts if p["id"] == post_id), None)
    if not post:
        return "Not Found", 404

    if request.method == "POST":
        post["thumbnail"] = request.form.get("thumbnail", "")
        post["title"] = request.form["title"]
        post["subtitle"] = request.form.get("subtitle", "")
        post["content"] = request.form["content"]

        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=S3_BLOG_FILE,
            Body=json.dumps(posts, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json"
        )
        return redirect(f"/post/{post_id}")

    return render_template("post_form.html", post=post)

#記事の削除画面
@app.route("/admin/delete/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    check = admin_required()
    if check:
        return check

    posts = load_blog_posts()
    posts = [p for p in posts if p["id"] != post_id]

    reflect_changeresult_of_blog_posts(posts)
    return redirect("/")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_LOGIN_KEY:
            session["admin"] = True
            return redirect("/")
        return "Forbidden", 403

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/")


@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500

@app.after_request
def add_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp





if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)