import os
import sqlite3
import logging
import bcrypt

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/database_manager.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

# 数据库文件路径
DB_PATH = "/config/data.db"

# 定义状态码
CONFIG_DEFAULT = 0
CONFIG_MODIFIED = 1

def hash_password(password):
    """使用 bcrypt 对密码进行哈希"""
    salt = bcrypt.gensalt()  # 生成盐值
    hashed = bcrypt.hashpw(password.encode(), salt)  # 使用 bcrypt 哈希密码
    return hashed.decode()  # 返回解码后的字符串以便存储

def initialize_database():
    """
    初始化数据库，检查是否存在并创建或更新表结构。
    """
    # 检查数据库文件是否存在
    if not os.path.exists(DB_PATH):
        logging.info("数据库文件不存在，正在创建...")
        create_tables()
        return CONFIG_DEFAULT
    else:
        logging.info("数据库文件已存在，正在检查表结构...")
        check_and_update_tables()
        return check_config_data()

def create_tables():
    """
    创建所有表结构，并检查插入默认数据。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 创建USERS表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS USERS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            USERNAME TEXT NOT NULL,
            NICKNAME TEXT,
            AVATAR_URL TEXT,
            PASSWORD TEXT NOT NULL,
            UNIQUE(USERNAME)
        )
    ''')

    # 创建CONFIG表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS CONFIG (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            OPTION TEXT NOT NULL,
            VALUE TEXT,
            UNIQUE(OPTION)
        )
    ''')

    # 创建LIB_MOVIES表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS LIB_MOVIES (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT NOT NULL,
            YEAR INTEGER,
            TMDB_ID INTEGER,
            DOUBAN_ID INTEGER,
            UNIQUE(TITLE, YEAR)
        )
    ''')

    # 创建LIB_TVS表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS LIB_TVS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT NOT NULL,
            YEAR INTEGER,
            TMDB_ID INTEGER,
            DOUBAN_ID INTEGER,
            UNIQUE(TITLE, YEAR)
        )
    ''')

    # 创建LIB_TV_SEASONS表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS LIB_TV_SEASONS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TV_ID INTEGER NOT NULL,
            SEASON INTEGER NOT NULL,
            YEAR INTEGER,
            EPISODES INTEGER,
            FOREIGN KEY (TV_ID) REFERENCES LIB_TVS(ID)
        )
    ''')

    # 创建RSS_MOVIES表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS RSS_MOVIES (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT NOT NULL,
            DOUBAN_ID INTEGER,
            YEAR INTEGER,
            SUB_TITLE TEXT,
            URL TEXT,
            UNIQUE(TITLE, YEAR)
        )
    ''')

    # 创建RSS_TVS表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS RSS_TVS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT NOT NULL,
            DOUBAN_ID INTEGER,
            YEAR INTEGER,
            SUB_TITLE TEXT,
            SEASON INTEGER,
            EPISODE INTEGER,
            URL TEXT,
            UNIQUE(TITLE, YEAR)
        )
    ''')

    # 创建MISS_MOVIES表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS MISS_MOVIES (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT NOT NULL,
            YEAR INTEGER,
            DOUBAN_ID INTEGER,
            UNIQUE(TITLE, YEAR)
        )
    ''')

    # 创建MISS_TVS表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS MISS_TVS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT NOT NULL,
            YEAR INTEGER,
            SEASON INTEGER,
            MISSING_EPISODES TEXT,
            DOUBAN_ID INTEGER,
            UNIQUE(TITLE, YEAR)
        )
    ''')

    # 插入默认用户数据
    cursor.execute("SELECT COUNT(*) FROM USERS WHERE USERNAME = 'admin'")
    if cursor.fetchone()[0] == 0:
        hashed_password = hash_password("password")
        cursor.execute('''
            INSERT INTO USERS (USERNAME, NICKNAME, AVATAR_URL, PASSWORD)
            VALUES (?, ?, ?, ?)
        ''', (
            "admin",
            "管理员",
            '/static/img/avatar.png',
            hashed_password
        ))

    # 插入默认配置数据
    default_configs = [
        ("notification", "False"),
        ("notification_api_key", "your_api_key"),
        ("dateadded", "False"),
        ("nfo_exclude_dirs", "Season,Movie,Music,Unknown,backdrops,.actors,.deletedByTMM"),
        ("nfo_excluded_filenames", "season.nfo,video1.nfo"),
        ("nfo_excluded_subdir_keywords", "Season,Music,Unknown,backdrops,.actors,.deletedByTMM"),
        ("media_dir", "/Media"),
        ("movies_path", "/Media/Movie"),
        ("episodes_path", "/Media/Episodes"),
        ("download_dir", "/Downloads"),
        ("download_action", "copy"),
        ("download_excluded_filenames", "【更多"),
        ("douban_api_key", "0ac44ae016490db2204ce0a042db2916"),
        ("douban_cookie", "your_douban_cookie_here"),
        ("douban_rss_url", "https://www.douban.com/feed/people/your_douban_id/interests"),
        ("tmdb_base_url", "https://api.tmdb.org"),
        ("tmdb_api_key", "your_api_key"),
        ("download_mgmt", "False"),
        ("download_type", "transmission"),
        ("download_username", "username"),
        ("download_password", "password"),
        ("download_host", "download_ip"),
        ("download_port", "port"),
        ("bt_login_username", "username"),
        ("bt_login_password", "password"),
        ("preferred_resolution", "1080p"),
        ("fallback_resolution", "2160p"),
        ("resources_exclude_keywords", "120帧,60帧,高码版,杜比视界,hdr"),
        ("bt_movie_base_url", "https://100.tudoutudou.top"),
        ("bt_tv_base_url", "https://200.tudoutudou.top"),
        ("run_interval_hours", "6")
    ]

    for option, value in default_configs:
        cursor.execute("SELECT COUNT(*) FROM CONFIG WHERE OPTION = ?", (option,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO CONFIG (OPTION, VALUE) VALUES (?, ?)", (option, value))

    conn.commit()
    conn.close()
    logging.info("数据库表结构及默认数据已创建。")

def check_and_update_tables():
    """
    检查表是否存在，如果不存在则创建。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 定义所有表名
    tables = [
        "USERS", "CONFIG", "LIB_MOVIES", "LIB_TVS", "LIB_TV_SEASONS",
        "RSS_MOVIES", "RSS_TVS", "MISS_MOVIES", "MISS_TVS"
    ]

    for table in tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        if cursor.fetchone() is None:
            logging.info(f"表 {table} 不存在，正在创建...")
            create_tables()
            break

    conn.close()

def check_config_data():
    """
    检查配置数据是否为默认数据。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    default_configs = {
        "notification_api_key": "your_api_key",
        "nfo_exclude_dirs": "Season,Movie,Music,Unknown,backdrops,.actors,.deletedByTMM",
        "nfo_excluded_filenames": "season.nfo,video1.nfo",
        "nfo_excluded_subdir_keywords": "Season,Music,Unknown,backdrops,.actors,.deletedByTMM",
        "media_dir": "/Media",
        "movies_path": "/Media/Movie",
        "episodes_path": "/Media/Episodes",
        "download_dir": "/Downloads",
        "download_action": "copy",
        "download_excluded_filenames": "【更多",
        "douban_api_key": "0ac44ae016490db2204ce0a042db2916",
        "douban_cookie": "your_douban_cookie_here",
        "douban_rss_url": "https://www.douban.com/feed/people/your_douban_id/interests",
        "tmdb_base_url": "https://api.tmdb.org",
        "tmdb_api_key": "your_api_key",
        "dateadded": "False",
        "download_mgmt": "False",
        "download_type": "transmission",
        "download_username": "username",
        "download_password": "password",
        "download_host": "download_ip",
        "download_port": "port",
        "bt_login_username": "username",
        "bt_login_password": "password",
        "preferred_resolution": "1080p",
        "fallback_resolution": "2160p",
        "resources_exclude_keywords": "120帧,60帧,高码版,杜比视界,hdr",
        "bt_movie_base_url": "https://100.tudoutudou.top",
        "bt_tv_base_url": "https://200.tudoutudou.top",
        "run_interval_hours": "6"
    }

    for option, value in default_configs.items():
        cursor.execute("SELECT VALUE FROM CONFIG WHERE OPTION = ?", (option,))
        row = cursor.fetchone()
        if row is None or row[0] != value:
            conn.close()
            return CONFIG_MODIFIED

    conn.close()
    return CONFIG_DEFAULT

if __name__ == "__main__":
    status_code = initialize_database()
    logging.info(f"数据库初始化状态码: {status_code}")