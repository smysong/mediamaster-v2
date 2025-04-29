import re
import sqlite3
import subprocess
import threading
import requests
import bcrypt
import psutil
from flask import Flask, g, render_template, request, redirect, url_for, jsonify, session, flash, session, Response
from functools import wraps
from werkzeug.exceptions import InternalServerError
from manual_search import MediaDownloader  # 导入 MediaDownloader 类
from datetime import timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from flask import stream_with_context
from transmission_rpc import Client as TransmissionClient
from qbittorrentapi import Client as QbittorrentClient
import os
import time
import logging

# 配置日志
# 创建独立的 logger 实例
logger = logging.getLogger("MediaMasterLogger")
logger.setLevel(logging.INFO)

# 禁用日志传播
logger.propagate = False

# 配置日志处理器
if not logger.handlers:
    file_handler = logging.FileHandler("/tmp/log/app.log", mode='w')
    stream_handler = logging.StreamHandler()

    # 设置日志格式
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    # 添加处理器到 logger
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
# 定义版本号
def get_app_version():
    """
    从 versions 文件中读取版本号
    """
    try:
        with open("versions", "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        logger.warning("versions 文件未找到，使用默认版本号")
        return "unknown"

APP_VERSION = get_app_version()
downloader = MediaDownloader()
app.secret_key = 'mediamaster'  # 设置一个密钥，用于会话管理
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)  # 设置会话有效期为24小时
app.config['SESSION_COOKIE_NAME'] = 'mediamaster'  # 设置会话 cookie 名称为 mediamaster
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # 设置会话 cookie 的 SameSite 属性
DATABASE = '/config/data.db'

# 存储进程ID的字典
running_services = {}

# 存储日志传输状态的字典
log_streaming_status = {}

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return view(**kwargs)
    return wrapped_view

def create_soft_link(src, dst):
    # 确保源目录存在
    os.makedirs(src, exist_ok=True)
    # 确保目标目录存在
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # 创建软链接
    if not os.path.exists(dst):
        os.symlink(src, dst)
        logger.info(f"软链接创建成功: {src} -> {dst}")
    else:
        logger.info(f"软链接已存在: {dst}")

@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        username = request.form['username']
        logger.info(f"用户 {username} 尝试登录")
        password = request.form['password']
        db = get_db()
        error = None
        user = db.execute('SELECT * FROM USERS WHERE USERNAME = ?', (username,)).fetchone()

        if user is None:
            error = '用户名或密码错误！'
        else:
            hashed_password = user['password']
            if not isinstance(hashed_password, str):
                hashed_password = hashed_password.decode('utf-8')

            if not bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                error = '用户名或密码错误！'

        if error is None:
            session.permanent = True
            session.clear()
            session['user_id'] = user['id']
            session['nickname'] = user['nickname']
            session['avatar_url'] = user['avatar_url']
            session['_permanent'] = True
            session.modified = True
            logger.info(f"用户 {username} 登录成功")
            return jsonify(success=True, message='登录成功。', redirect_url=url_for('dashboard'))

        logger.warning(f"用户 {username} 登录失败: {error}")
        return jsonify(success=False, message=error)

    return render_template('login.html', version=APP_VERSION)

@app.route('/logout')
def logout():
    nickname = session.get('nickname')
    logger.info(f"用户 {nickname} 登出")
    session.clear()
    return redirect(url_for('login'))

# 配置允许上传的文件类型
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# 检查文件扩展名是否合法
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    try:
        user_id = session['user_id']
        nickname = session.get('nickname')
        logger.info(f"用户 {nickname} 更新个人资料")
        db = get_db()

        # 获取表单数据
        username = request.form.get('username')
        avatar_file = request.files.get('avatar')

        # 标记是否更新了昵称或头像
        updated_nickname = False
        updated_avatar = False

        # 更新用户名
        if username:
            db.execute('UPDATE USERS SET nickname = ? WHERE id = ?', (username, user_id))
            logger.info(f"用户 {user_id} 更新了昵称: {username}")
            updated_nickname = True

        # 更新头像
        if avatar_file and allowed_file(avatar_file.filename):
            upload_folder = 'static/uploads/avatars'
            os.makedirs(upload_folder, exist_ok=True)
            filename = secure_filename(avatar_file.filename)
            file_path = os.path.join(upload_folder, filename)
            avatar_file.save(file_path)
            avatar_url = f"/{upload_folder}/{filename}"
            db.execute('UPDATE USERS SET avatar_url = ? WHERE id = ?', (avatar_url, user_id))
            logger.info(f"用户 {nickname} 更新了头像: {avatar_url}")
            updated_avatar = True
        elif not avatar_file:
            logger.info(f"用户 {nickname} 未选择新头像，跳过头像更新")

        # 提交数据库更改
        db.commit()
        logger.info(f"用户 {nickname} 个人资料更新成功")

        # 更新会话中的昵称和头像
        if updated_nickname:
            session['nickname'] = username
        if updated_avatar:
            session['avatar_url'] = avatar_url

        # 构造返回消息
        if updated_nickname and updated_avatar:
            message = '昵称和头像已更新！'
        elif updated_nickname:
            message = '昵称已更新！'
        elif updated_avatar:
            message = '头像已更新！'
        else:
            message = '无需更新！'

        return jsonify(success=True, message=message)

    except Exception as e:
        db.rollback()
        logger.error(f"用户 {nickname} 更新个人资料失败: {e}")
        return jsonify(success=False, message='更新失败，请稍后再试。'), 500

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    user_id = session['user_id']
    nickname = session.get('nickname')
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        logger.info(f"用户 {nickname} 尝试修改密码")

        db = get_db()
        user = db.execute('SELECT * FROM USERS WHERE ID = ?', (user_id,)).fetchone()

        hashed_password = user['password']
        if not isinstance(hashed_password, str):
            hashed_password = hashed_password.decode('utf-8')

        if user and bcrypt.checkpw(old_password.encode('utf-8'), hashed_password.encode('utf-8')):
            new_hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            db.execute('UPDATE USERS SET PASSWORD = ? WHERE ID = ?', (new_hashed_password, user_id))
            db.commit()
            logger.info(f"用户 {nickname} 密码修改成功")
            return jsonify(success=True, message='您的密码已成功更新。', redirect_url=url_for('dashboard'))

        logger.warning(f"用户 {nickname} 密码修改失败: 旧密码错误")
        return jsonify(success=False, message='旧密码错误。', redirect_url=url_for('change_password'))

    return render_template('change_password.html', nickname=session.get('nickname'), avatar_url=session.get('avatar_url'), version=APP_VERSION)

@app.errorhandler(InternalServerError)
def handle_500(error):
    logger.error(f"服务器错误: {error}")
    return render_template('500.html'), 500

@app.route('/')
@login_required
def dashboard():
    db = get_db()
    
    # 获取电影数量
    total_movies = db.execute('SELECT COUNT(*) FROM LIB_MOVIES').fetchone()[0]
    
    # 获取电视剧数量
    total_tvs = db.execute('SELECT COUNT(DISTINCT id) FROM LIB_TVS').fetchone()[0]
    
    # 获取剧集数量
    total_episodes = db.execute('SELECT SUM(LENGTH(episodes) - LENGTH(REPLACE(episodes, \',\', \'\')) + 1) FROM LIB_TV_SEASONS').fetchone()[0] or 0
     
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    
    return render_template('dashboard.html', 
                           total_movies=total_movies, 
                           total_tvs=total_tvs, 
                           total_episodes=total_episodes, 
                           nickname=nickname, 
                           avatar_url=avatar_url, 
                           version=APP_VERSION)

@app.route('/api/system_resources', methods=['GET'])
@login_required
def system_resources():
    # 获取存储空间信息
    disk_usage = psutil.disk_usage('/Media')
    disk_total_gb = disk_usage.total / (1024 ** 3)  # 总容量，单位为GB
    disk_used_gb = disk_usage.used / (1024 ** 3)    # 已用容量，单位为GB
    disk_usage_percent = disk_usage.percent         # 使用百分比

    # 获取 CPU 利用率
    cpu_usage_percent = psutil.cpu_percent(interval=1)

    # 获取 CPU 数量和核心数
    cpu_count_logical = psutil.cpu_count(logical=True)  # 逻辑 CPU 数量
    cpu_count_physical = psutil.cpu_count(logical=False)  # 物理 CPU 核心数

    # 获取内存信息
    memory = psutil.virtual_memory()
    memory_total_gb = memory.total / (1024 ** 3)  # 内存总量，单位为GB
    memory_used_gb = memory.used / (1024 ** 3)    # 已用内存，单位为GB
    memory_usage_percent = memory.percent         # 内存使用百分比

    # 获取下载器客户端
    try:
        client = get_downloader_client()
        if isinstance(client, TransmissionClient):
            torrents = client.get_torrents()
            net_io_recv_per_sec = sum(t.rate_download for t in torrents) / 1024  # 转换为 KB/s
            net_io_sent_per_sec = sum(t.rate_upload for t in torrents) / 1024    # 转换为 KB/s
        elif isinstance(client, QbittorrentClient):
            torrents = client.torrents_info()
            net_io_recv_per_sec = sum(t.dlspeed for t in torrents) / 1024  # 转换为 KB/s
            net_io_sent_per_sec = sum(t.upspeed for t in torrents) / 1024    # 转换为 KB/s
        else:
            net_io_sent_per_sec = 0
            net_io_recv_per_sec = 0
    except Exception as e:
        logger.error(f"获取下载器信息失败: {e}")
        net_io_sent_per_sec = 0
        net_io_recv_per_sec = 0

    # 返回系统资源数据
    return jsonify({
        "disk_total_gb": round(disk_total_gb, 2),         # 存储空间总量（GB）
        "disk_used_gb": round(disk_used_gb, 2),           # 存储空间已用容量（GB）
        "disk_usage_percent": disk_usage_percent,         # 存储空间使用百分比
        "net_io_sent": round(net_io_sent_per_sec, 2),   # 网络上传速率（KB/s）
        "net_io_recv": round(net_io_recv_per_sec, 2),   # 网络下载速率（KB/s）
        "cpu_usage_percent": cpu_usage_percent,           # CPU 利用率
        "cpu_count_logical": cpu_count_logical,           # 逻辑 CPU 数量
        "cpu_count_physical": cpu_count_physical,         # 物理 CPU 核心数
        "memory_total_gb": round(memory_total_gb, 2),     # 内存总量（GB）
        "memory_used_gb": round(memory_used_gb, 2),       # 已用内存（GB）
        "memory_usage_percent": memory_usage_percent      # 内存使用百分比
    })

@app.route('/api/system_processes', methods=['GET'])
@login_required
def system_processes():
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_percent', 'create_time']):
        try:
            # 计算运行时长
            uptime = time.time() - proc.info['create_time']
            # 将运行时长格式化为小时、分钟、秒
            uptime_formatted = time.strftime('%H:%M:%S', time.gmtime(uptime))
            
            # 获取命令行参数
            cmdline = proc.info['cmdline']
            
            # 初始化文件名为 None
            file_name = None
            
            # 如果进程名为 'python' 或 'python3'，则尝试获取文件名
            if proc.info['name'] in ['python', 'python3'] and len(cmdline) > 1:
                file_name = os.path.basename(cmdline[1])
            
            # 添加进程信息到列表
            processes.append({
                "pid": proc.info['pid'],
                "name": proc.info['name'],
                "file_name": file_name,
                "cpu_percent": proc.info['cpu_percent'],
                "memory_percent": proc.info['memory_percent'],
                "uptime": uptime_formatted
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # 忽略不存在的进程、访问被拒绝的进程和僵尸进程
            continue

    return jsonify({
        "processes": processes
    })

@app.route('/recommendations')
@login_required
def recommendations():
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    db = get_db()
    # 从数据库中读取 tmdb_api_key
    tmdb_api_key = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('tmdb_api_key',)).fetchone()
    tmdb_api_key = tmdb_api_key['VALUE'] if tmdb_api_key else None
    return render_template('recommendations.html', nickname=nickname, avatar_url=avatar_url, tmdb_api_key=tmdb_api_key, version=APP_VERSION)

@app.route('/library')
@login_required
def library():
    try:
        db = get_db()
        page = int(request.args.get('page', 1))
        per_page = 24
        offset = (page - 1) * per_page
        media_type = request.args.get('type', 'movies')

        # 获取电影或电视剧的总数
        total_movies = db.execute('SELECT COUNT(*) FROM LIB_MOVIES').fetchone()[0]
        total_tvs = db.execute('SELECT COUNT(DISTINCT id) FROM LIB_TVS').fetchone()[0]

        if media_type == 'movies':
            movies = db.execute('SELECT id, title, year, tmdb_id FROM LIB_MOVIES ORDER BY year DESC LIMIT ? OFFSET ?', (per_page, offset)).fetchall()
            tv_data = []
        elif media_type == 'tvs':
            movies = []
            # 查询电视剧基本信息
            tv_ids = db.execute('SELECT id FROM LIB_TVS ORDER BY year DESC LIMIT ? OFFSET ?', (per_page, offset)).fetchall()
            tv_ids = [tv['id'] for tv in tv_ids]

            # 获取这些电视剧的所有季信息
            tv_seasons = db.execute('''
                SELECT t1.id, t1.title, t2.season, t2.episodes, t1.year, t1.tmdb_id
                FROM LIB_TVS AS t1 
                JOIN LIB_TV_SEASONS AS t2 ON t1.id = t2.tv_id 
                WHERE t1.id IN ({})
                ORDER BY t1.year DESC, t1.id, t2.season 
            '''.format(','.join(['?'] * len(tv_ids))), tv_ids).fetchall()

            # 将相同电视剧的季信息合并，并计算总集数
            tv_data = {}
            for tv in tv_seasons:
                if tv['id'] not in tv_data:
                    tv_data[tv['id']] = {
                        'id': tv['id'],
                        'title': tv['title'],
                        'year': tv['year'],
                        'tmdb_id': tv['tmdb_id'],
                        'seasons': [],
                        'total_episodes': 0
                    }
                
                # 解析 episodes 字符串，计算总集数
                episodes_list = tv['episodes'].split(',')
                num_episodes = len(episodes_list)

                tv_data[tv['id']]['seasons'].append({
                    'season': tv['season'],
                    'episodes': num_episodes  # 季的集数
                })
                tv_data[tv['id']]['total_episodes'] += num_episodes  # 累加总集数
            tv_data = list(tv_data.values())
        else:
            movies = []
            tv_data = []

        # 从数据库中读取 tmdb_api_key
        tmdb_api_key = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('tmdb_api_key',)).fetchone()
        tmdb_api_key = tmdb_api_key['VALUE'] if tmdb_api_key else None

        # 从会话中获取用户昵称和头像
        nickname = session.get('nickname')
        avatar_url = session.get('avatar_url')

        return render_template('library.html', 
                               movies=movies, 
                               tv_data=tv_data, 
                               page=page, 
                               per_page=per_page, 
                               total_movies=total_movies, 
                               total_tvs=total_tvs, 
                               media_type=media_type, 
                               tmdb_api_key=tmdb_api_key,
                               nickname=nickname,
                               avatar_url=avatar_url,
                               version=APP_VERSION)
    except Exception as e:
        logger.error(f"发生错误: {e}")
        raise InternalServerError("发生意外错误，请稍后再试。")

@app.route('/subscriptions')
@login_required
def subscriptions():
    db = get_db()
    miss_movies = db.execute('SELECT * FROM MISS_MOVIES').fetchall()
    miss_tvs = db.execute('SELECT * FROM MISS_TVS').fetchall()
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('subscriptions.html', miss_movies=miss_movies, miss_tvs=miss_tvs, nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/douban_subscriptions')
@login_required
def douban_subscriptions():
    db = get_db()
    rss_movies = db.execute('SELECT * FROM RSS_MOVIES').fetchall()
    rss_tvs = db.execute('SELECT * FROM RSS_TVS').fetchall()
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('douban_subscriptions.html', rss_movies=rss_movies, rss_tvs=rss_tvs, nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/tmdb_subscriptions', methods=['POST'])
@login_required
def tmdb_subscriptions():
    try:
        # 获取请求数据
        data = request.json
        title = data.get('title')
        year = data.get('year')
        season = data.get('season')  # 如果是电视剧，获取季编号
        episodes = data.get('episodes')  # 如果是电视剧，获取总集数

        # 检查必要字段
        if not title or not year:
            return jsonify({"success": False, "message": "缺少必要的订阅信息"}), 400

        db = get_db()

        if season and episodes:  # 如果包含季编号和集数，则为电视剧订阅
            # 生成缺失的集数字符串，例如 "1,2,3,...,episodes"
            missing_episodes = ','.join(map(str, range(1, episodes + 1)))

            # 检查是否已存在相同的订阅
            existing_tv = db.execute(
                'SELECT * FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                (title, year, season)
            ).fetchone()

            if existing_tv:
                return jsonify({"success": False, "message": "该电视剧订阅已存在"}), 400

            # 插入电视剧订阅
            db.execute(
                'INSERT INTO MISS_TVS (title, year, season, missing_episodes) VALUES (?, ?, ?, ?)',
                (title, year, season, missing_episodes)
            )
            db.commit()
            return jsonify({"success": True, "message": "电视剧订阅成功"})

        else:  # 否则为电影订阅
            # 检查是否已存在相同的订阅
            existing_movie = db.execute(
                'SELECT * FROM MISS_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            ).fetchone()

            if existing_movie:
                return jsonify({"success": False, "message": "该电影订阅已存在"}), 400

            # 插入电影订阅
            db.execute(
                'INSERT INTO MISS_MOVIES (title, year) VALUES (?, ?)',
                (title, year)
            )
            db.commit()
            return jsonify({"success": True, "message": "电影订阅成功"})

    except Exception as e:
        logger.error(f"订阅处理失败: {e}")
        return jsonify({"success": False, "message": "订阅失败，请稍后再试"}), 500

@app.route('/check_subscriptions', methods=['POST'])
@login_required
def check_subscriptions():
    try:
        data = request.json
        title = data.get('title')
        year = data.get('year')
        season = data.get('season')  # 如果是电视剧，获取季编号

        db = get_db()

        if season:  # 检查电视剧订阅
            existing_tv = db.execute(
                'SELECT * FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                (title, year, season)
            ).fetchone()
            if existing_tv:
                return jsonify({"subscribed": True})
        else:  # 检查电影订阅
            existing_movie = db.execute(
                'SELECT * FROM MISS_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            ).fetchone()
            if existing_movie:
                return jsonify({"subscribed": True})

        return jsonify({"subscribed": False})
    except Exception as e:
        logger.error(f"检查订阅状态失败: {e}")
        return jsonify({"subscribed": False, "error": "检查失败"}), 500

@app.route('/search', methods=['GET'])
@login_required
def search():
    db = get_db()
    query = request.args.get('q', '').strip()
    results = []

    if query:
        # 查询电影并按年份排序
        movies = db.execute('SELECT * FROM LIB_MOVIES WHERE title LIKE ? ORDER BY year ASC', ('%' + query + '%',)).fetchall()
        
        # 查询电视剧并获取其季信息
        tvs = db.execute('SELECT * FROM LIB_TVS WHERE title LIKE ? ORDER BY title ASC', ('%' + query + '%',)).fetchall()

        # 合并结果
        for movie in movies:
            results.append({
                'type': 'movie',
                'id': movie['id'],
                'title': movie['title'],
                'year': movie['year']
            })

        for tv in tvs:
            # 获取该电视剧的所有季信息，并按季数排序
            seasons = db.execute('SELECT season, episodes FROM LIB_TV_SEASONS WHERE tv_id = ? ORDER BY season ASC', (tv['id'],)).fetchall()
            tv_data = {
                'type': 'tv',
                'id': tv['id'],
                'title': tv['title'],
                'seasons': seasons
            }
            results.append(tv_data)
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('search_results.html', query=query, results=results, nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/edit_subscription/<type>/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_subscription(type, id):
    db = get_db()
    if type == 'movie':
        subscription = db.execute('SELECT * FROM MISS_MOVIES WHERE id = ?', (id,)).fetchone()
    elif type == 'tv':
        subscription = db.execute('SELECT * FROM MISS_TVS WHERE id = ?', (id,)).fetchone()
    else:
        return "Invalid subscription type", 400

    if request.method == 'POST':
        title = request.form['title']
        year = request.form['year'] if type == 'movie' else None
        season = request.form['season'] if type == 'tv' else None
        missing_episodes = request.form['missing_episodes'] if type == 'tv' else None

        if type == 'movie':
            db.execute('UPDATE MISS_MOVIES SET title = ?, year = ? WHERE id = ?', (title, year, id))
        elif type == 'tv':
            db.execute('UPDATE MISS_TVS SET title = ?, season = ?, missing_episodes = ? WHERE id = ?', (title, season, missing_episodes, id))
        db.commit()
        return redirect(url_for('subscriptions'))
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('edit_subscription.html', subscription=subscription, type=type, nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/delete_subscription/<type>/<int:id>', methods=['POST'])
@login_required
def delete_subscription(type, id):
    db = get_db()
    if type == 'movie':
        db.execute('DELETE FROM MISS_MOVIES WHERE id = ?', (id,))
    elif type == 'tv':
        db.execute('DELETE FROM MISS_TVS WHERE id = ?', (id,))
    else:
        return "Invalid subscription type", 400
    db.commit()
    return redirect(url_for('subscriptions'))

@app.route('/service_control')
@login_required
def service_control():
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('service_control.html', nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

def run_script_and_cleanup(process, log_file_path):
    process.wait()  # 等待子进程完成
    if os.path.exists(log_file_path):
        os.remove(log_file_path)  # 删除日志文件

@app.route('/run_service', methods=['POST'])
@login_required
def run_service():
    data = request.get_json()
    service = data.get('service')
    try:
        logger.info(f"尝试启动服务: {service}")
        log_file_path = f'/tmp/log/{service}.log'
        with open(log_file_path, 'w', encoding='utf-8') as log_file:
            process = subprocess.Popen(['python3', f'/app/{service}.py'], stdout=log_file, stderr=log_file)
            pid = process.pid
            running_services[service] = pid
            threading.Thread(target=run_script_and_cleanup, args=(process, log_file_path)).start()
        logger.info(f"服务 {service} 启动成功，PID: {pid}")
        return jsonify({"message": "服务运行成功！", "pid": pid}), 200
    except Exception as e:
        logger.error(f"服务 {service} 启动失败: {e}")
        return jsonify({"message": str(e)}), 500

@app.route('/realtime_log/<string:service>')
@login_required
def realtime_log(service):
    @stream_with_context
    def generate():
        log_file_path = f'/tmp/log/{service}.log'
        if not os.path.exists(log_file_path):
            logger.warning(f"实时日志文件不存在: {log_file_path}")
            yield 'data: 当前没有实时运行日志，请检查服务是否正在运行！\n\n'.encode('utf-8')
            return
        
        # 检查文件是否为空
        if os.path.getsize(log_file_path) == 0:
            logger.warning(f"实时日志文件为空: {log_file_path}")
            yield 'data: 当前日志文件为空\n\n'.encode('utf-8')
            return

        logger.info(f"开始读取实时日志: {log_file_path}")
        with open(log_file_path, 'r', encoding='utf-8') as log_file:
            while True:
                line = log_file.readline()
                if not line:
                    time.sleep(0.1)
                    # 检查是否需要停止日志传输
                    if not log_streaming_status.get(service, True):
                        logger.info(f"停止读取日志: {log_file_path}")
                        break
                    continue
                yield f'data: {line}\n\n'
    log_streaming_status[service] = True  # 初始化日志传输状态为 True
    return Response(generate(), mimetype='text/event-stream', content_type='text/event-stream; charset=utf-8')

@app.route('/stop_realtime_log/<string:service>', methods=['POST'])
@login_required
def stop_realtime_log(service):
    try:
        log_streaming_status[service] = False  # 设置日志传输状态为 False
        logger.info(f"停止实时日志传输: {service}")
        return jsonify({"message": "实时日志传输已停止"}), 200
    except Exception as e:
        logger.error(f"停止实时日志传输失败: {e}")
        return jsonify({"message": "停止实时日志传输失败"}), 500

# 手动搜索和下载接口
@app.route('/manual_search')
@login_required
def manual_search():
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    logger.info(f"用户 {nickname} 访问手动搜索页面")
    return render_template('manual_search.html', nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/api/search_movie', methods=['POST'])
@login_required
def api_search_movie():
    data = request.json
    keyword = data.get('keyword')
    year = data.get('year')
    nickname = session.get('nickname')

    if not keyword:
        logger.warning(f"用户 {nickname} 搜索电影失败: 缺少关键词")
        return jsonify({'error': '缺少关键词'}), 400

    logger.info(f"用户 {nickname} 搜索电影: 关键词={keyword}, 年份={year}")
    try:
        results = downloader.search_movie(keyword, year)
        logger.info(f"用户 {nickname} 搜索电影成功: 返回结果数量={len(results)}")
        return jsonify(results)
    except Exception as e:
        logger.error(f"用户 {nickname} 搜索电影失败: {e}")
        return jsonify({'error': '搜索失败，请稍后再试。'}), 500

@app.route('/api/search_tv_show', methods=['POST'])
@login_required
def api_search_tv_show():
    data = request.json
    keyword = data.get('keyword')
    year = data.get('year')
    nickname = session.get('nickname')

    if not keyword:
        logger.warning(f"用户 {nickname} 搜索电视剧失败: 缺少关键词")
        return jsonify({'error': '缺少关键词'}), 400

    logger.info(f"用户 {nickname} 搜索电视剧: 关键词={keyword}, 年份={year}")
    try:
        results = downloader.search_tv_show(keyword, year)
        logger.info(f"用户 {nickname} 搜索电视剧成功: 返回结果数量={len(results)}")
        return jsonify(results)
    except Exception as e:
        logger.error(f"用户 {nickname} 搜索电视剧失败: {e}")
        return jsonify({'error': '搜索失败，请稍后再试。'}), 500

@app.route('/api/download_movie', methods=['GET'])
@login_required
def api_download_movie():
    link = request.args.get('link')
    title = request.args.get('title')
    year = request.args.get('year')
    nickname = session.get('nickname')

    if not link or not title or not year:
        logger.warning(f"用户 {nickname} 下载电影失败: 缺少参数")
        return jsonify({'error': '缺少参数'}), 400

    logger.info(f"用户 {nickname} 尝试下载电影: 标题={title}, 年份={year}, 链接={link}")
    try:
        success = downloader.download_movie(link, title, year)
        if success:
            logger.info(f"用户 {nickname} 下载电影成功: 标题={title}, 年份={year}")
            return jsonify({'success': True})
        else:
            logger.warning(f"用户 {nickname} 下载电影失败: 标题={title}, 年份={year}")
            return jsonify({'success': False}), 400
    except Exception as e:
        logger.error(f"用户 {nickname} 下载电影失败: {e}")
        return jsonify({'error': '下载失败，请稍后再试。'}), 500

@app.route('/api/download_tv_show', methods=['GET'])
@login_required
def api_download_tv_show():
    link = request.args.get('link')
    title = request.args.get('title')
    year = request.args.get('year')
    nickname = session.get('nickname')

    if not link or not title or not year:
        logger.warning(f"用户 {nickname} 下载电视剧失败: 缺少参数")
        return jsonify({'error': '缺少参数'}), 400

    logger.info(f"用户 {nickname} 尝试下载电视剧: 标题={title}, 年份={year}, 链接={link}")
    try:
        success = downloader.download_tv_show(link, title, year)
        if success:
            logger.info(f"用户 {nickname} 下载电视剧成功: 标题={title}, 年份={year}")
            return jsonify({'success': True})
        else:
            logger.warning(f"用户 {nickname} 下载电视剧失败: 标题={title}, 年份={year}")
            return jsonify({'success': False}), 400
    except Exception as e:
        logger.error(f"用户 {nickname} 下载电视剧失败: {e}")
        return jsonify({'error': '下载失败，请稍后再试。'}), 500

GROUP_MAPPING = {
    "定时任务": {
        "run_interval_hours": {"type": "text", "label": "程序运行间隔"}
    },
    "消息通知": {
        "notification": {"type": "switch", "label": "消息通知"},
        "notification_api_key": {"type": "password", "label": "Bark设备Token"}
    },
    "媒体添加时间": {
        "dateadded": {"type": "switch", "label": "使用影片发行日期"}
    },
    "中文演职人员": {
        "nfo_exclude_dirs": {"type": "text", "label": "排除目录"},
        "nfo_excluded_filenames": {"type": "text", "label": "排除文件名"},
        "nfo_excluded_subdir_keywords": {"type": "text", "label": "排除关键字"}
    },
    "媒体库目录": {
        "media_dir": {"type": "text", "label": "主目录"},
        "movies_path": {"type": "text", "label": "电影目录"},
        "episodes_path": {"type": "text", "label": "电视剧目录"}
    },
    "资源下载设置": {
        "download_dir": {"type": "text", "label": "下载目录"},
        "download_action": {"type": "select", "label": "下载文件转移方式", "options": ["移动", "复制"]},
        "download_excluded_filenames": {"type": "text", "label": "下载转移排除的文件名"},
        "preferred_resolution": {"type": "text", "label": "资源下载首选分辨率"},
        "fallback_resolution": {"type": "text", "label": "资源下载备选分辨率"},
        "resources_exclude_keywords": {"type": "text", "label": "资源搜索排除关键词"}
    },
    "豆瓣设置": {
        "douban_api_key": {"type": "password", "label": "豆瓣API密钥"},
        "douban_cookie": {"type": "text", "label": "豆瓣COOKIE"},
        "douban_rss_url": {"type": "text", "label": "豆瓣订阅地址"}
    },
    "TMDB接口": {
        "tmdb_base_url": {"type": "text", "label": "TMDB API接口地址"},
        "tmdb_api_key": {"type": "password", "label": "TMDB API密钥"}
    },
    "下载器管理": {
        "download_mgmt": {"type": "switch", "label": "下载器管理"},
        "download_type": {"type": "downloader", "label": "下载器", "options": ["transmission", "qbittorrent"]},
        "download_username": {"type": "text", "label": "下载器用户名"},
        "download_password": {"type": "password", "label": "下载器密码"},
        "download_host": {"type": "text", "label": "下载器地址"},
        "download_port": {"type": "text", "label": "下载器端口"}
    },
    "资源站点设置": {
        "bt_login_username": {"type": "text", "label": "站点登录用户名"},
        "bt_login_password": {"type": "password", "label": "站点登录密码"},
        "bt_movie_login_url": {"type": "text", "label": "电影站点登录地址"},
        "bt_movie_search_url": {"type": "text", "label": "电影站点搜索地址"},
        "bt_tv_login_url": {"type": "text", "label": "电视剧站点登录地址"},
        "bt_movie_base_url": {"type": "text", "label": "电影站点"},
        "bt_tv_base_url": {"type": "text", "label": "电视剧站点"},
        "bt_tv_search_url": {"type": "text", "label": "电视剧站点搜索地址"}
    }
}
@app.route('/settings')
@login_required
def settings_page():
    # 从数据库读取配置项（包括 ID 字段）
    db = get_db()
    config_rows = db.execute('SELECT ID, OPTION, VALUE FROM CONFIG').fetchall()

    # 将配置项转换为新的分组数据结构
    grouped_config_data = {}
    for row in config_rows:
        option_id = row['ID']  # 获取 ID 字段
        option = row['OPTION']
        value = row['VALUE']

        # 遍历分组映射，找到对应的分组
        for group_name, group_items in GROUP_MAPPING.items():
            if option in group_items:
                if group_name not in grouped_config_data:
                    grouped_config_data[group_name] = {}
                grouped_config_data[group_name][option] = {
                    "id": option_id,  # 添加 ID 字段
                    "value": value,
                    **group_items[option]  # 合并类型和标签信息
                }
                break
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    # 渲染模板并传递分组后的配置数据
    return render_template('settings.html', config=grouped_config_data, nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/save_set', methods=['POST'])
@login_required
def save_settings():
    db = get_db()
    form_data = request.form
    try:
        for key, value in form_data.items():
            if not key.endswith('_id'):
                option_id = form_data.get(f"{key}_id")
                if option_id:
                    logger.info(f"更新配置项 ID={option_id}, KEY={key}, VALUE={value}")
                    db.execute('UPDATE CONFIG SET VALUE = ? WHERE ID = ?', (value, option_id))
        db.commit()
        logger.info("配置保存成功")
        flash('设置已成功保存！', 'success')
    except Exception as e:
        db.rollback()
        logger.error(f"配置保存失败: {e}")
        flash('设置保存失败，请稍后再试。', 'error')
    return redirect(url_for('settings_page'))

@app.route('/download_mgmt')
@login_required
def download_mgmt_page():
    db = get_db()
    # 从数据库中读取 download_mgmt 的配置
    download_mgmt_config = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('download_mgmt',)).fetchone()
    
    # 检查 download_mgmt 是否存在且为 True
    if not download_mgmt_config or download_mgmt_config['VALUE'] != 'True':
        flash('下载管理功能未启用，请在系统设置中开启下载管理功能。', 'error')
        return redirect(url_for('settings_page'))
    
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    
    # 将信息传递给模板
    return render_template('download_mgmt.html', nickname=nickname, avatar_url=avatar_url, download_mgmt=download_mgmt_config, version=APP_VERSION)


# 获取下载器客户端
def get_downloader_client():
    db = get_db()
    config_rows = db.execute('''
        SELECT OPTION, VALUE FROM CONFIG 
        WHERE OPTION IN (?, ?, ?, ?, ?)
    ''', ('download_type', 'download_host', 'download_port', 'download_username', 'download_password')).fetchall()

    config = {row['OPTION']: row['VALUE'] for row in config_rows}

    if not all(config.values()):
        raise ValueError("下载器配置不完整，请检查数据库中的配置项。")

    download_type = config['download_type']
    host = config['download_host']
    port = config['download_port']
    username = config['download_username']
    password = config['download_password']

    if download_type == 'transmission':
        return TransmissionClient(
            host=host,
            port=port,
            username=username,
            password=password
        )
    elif download_type == 'qbittorrent':
        return QbittorrentClient(
            host=f"http://{host}:{port}",
            username=username,
            password=password
        )
    else:
        raise ValueError(f"不支持的下载器类型: {download_type}")

# 获取任务列表
@app.route('/api/download/list', methods=['GET'])
@login_required
def list_torrents():
    try:
        client = get_downloader_client()

        if isinstance(client, TransmissionClient):
            torrents = client.get_torrents()
            result = [{
                "id": t.id,
                "name": t.name,
                "percentDone": t.percent_done,
                "status": t.status,
                "rateDownload": t.rate_download,
                "rateUpload": t.rate_upload,
                "magnetLink": t.magnet_link
            } for t in torrents]
        else:  # qBittorrent
            torrents = client.torrents_info()
            result = [{
                "id": t.hash,
                "name": t.name,
                "percentDone": t.progress,
                "status": t.state_enum.name.lower(),
                "rateDownload": t.dlspeed,
                "rateUpload": t.upspeed,
                "magnetLink": t.magnet_uri
            } for t in torrents]

        return jsonify({"torrents": result})
    except Exception as e:
        logger.error(f"获取任务列表失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download/add', methods=['POST'])
@login_required
def add_torrent():
    try:
        data = request.json
        client = get_downloader_client()

        task_type = data.get("type")
        task_value = data.get("value")

        if task_type == "url":
            # 直接尝试添加磁力链接任务
            if isinstance(client, TransmissionClient):
                client.add_torrent(torrent=task_value)
            else:
                client.torrents_add(urls=task_value)

        elif task_type == "base64":
            # 解码Base64字符串并添加种子文件任务
            import base64
            try:
                # 解码Base64字符串
                torrent_data = base64.b64decode(task_value)
            except Exception as e:
                logger.error(f"Base64解码失败: {e}")
                return jsonify({"error": "无效的Base64数据"}), 400

            # 添加种子文件任务
            if isinstance(client, TransmissionClient):
                client.add_torrent(torrent=torrent_data)
            else:
                client.torrents_add(torrent_files=[torrent_data])

        else:
            return jsonify({"error": "无效的添加类型"}), 400

        return jsonify({"message": "添加成功"})
    except Exception as e:
        logger.error(f"添加任务失败: {e}")
        return jsonify({"error": str(e)}), 500

# 批量操作（启动、暂停、删除）
@app.route('/api/download/<action>', methods=['POST'])
@login_required
def bulk_action(action):
    try:
        data = request.json
        client = get_downloader_client()

        # 获取任务 ID 列表
        task_ids = data.get("ids", [])
        if not task_ids:
            return jsonify({"error": "任务 ID 列表为空"}), 400

        # 根据下载器类型处理任务 ID
        if isinstance(client, TransmissionClient):
            # Transmission 使用整数 ID
            task_ids = [int(task_id) for task_id in task_ids]
        else:
            # qBittorrent 使用 SHA-1 哈希值
            task_ids = [str(task_id) for task_id in task_ids]

        # 执行批量操作
        if action == "start":
            if isinstance(client, TransmissionClient):
                client.start_torrent(task_ids)
            else:
                client.torrents_resume(hashes=task_ids)
        elif action == "pause":
            if isinstance(client, TransmissionClient):
                client.stop_torrent(task_ids)
            else:
                client.torrents_pause(hashes=task_ids)
        elif action == "delete":
            if isinstance(client, TransmissionClient):
                client.remove_torrent(task_ids, delete_data=True)
            else:
                client.torrents_delete(delete_files=True, hashes=task_ids)
        else:
            return jsonify({"error": "无效的操作"}), 400

        return jsonify({"message": f"{action} 成功"})
    except Exception as e:
        logger.error(f"批量操作失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download/get-magnet-links', methods=['POST'])
@login_required
def get_magnet_links():
    try:
        # 获取请求数据
        data = request.json
        task_ids = data.get("ids", [])

        # 检查任务 ID 列表是否为空
        if not task_ids:
            return jsonify({"error": "任务 ID 列表为空"}), 400

        # 获取下载器客户端
        client = get_downloader_client()

        # 根据下载器类型校验任务 ID 格式
        if isinstance(client, TransmissionClient):
            # Transmission 使用整数 ID
            try:
                task_ids = [int(task_id) for task_id in task_ids]
            except ValueError:
                return jsonify({"error": "无效的任务 ID，应为整数"}), 400
        else:
            # qBittorrent 使用 SHA-1 哈希值
            for task_id in task_ids:
                if not re.match(r'^[a-fA-F0-9]{40}$', task_id):
                    return jsonify({"error": f"无效的任务 ID: {task_id}，应为 40 字符的 SHA-1 哈希值"}), 400

        # 获取磁力链接
        magnet_links = []

        if isinstance(client, TransmissionClient):
            # Transmission 获取磁力链接
            torrents = client.get_torrents(ids=task_ids)
            for torrent in torrents:
                magnet_links.append(torrent.magnet_link)
        else:
            # qBittorrent 获取磁力链接
            for task_id in task_ids:
                try:
                    magnet_link = client.torrents_info(hashes=[task_id])[0].magnet_uri
                    magnet_links.append(magnet_link)
                except IndexError:
                    logger.warning(f"任务 ID {task_id} 未找到对应的磁力链接")
                    continue

        return jsonify({"magnetLinks": magnet_links})
    except Exception as e:
        logger.error(f"获取磁力链接失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/check_update', methods=['GET'])
@login_required
def check_update():
    try:
        # 当前版本号
        current_version = APP_VERSION

        # 获取 GitHub 仓库的最新版本信息
        repo_url = "https://api.github.com/repos/smysong/mediamaster-v2/releases/latest"
        response = requests.get(repo_url)
        if response.status_code != 200:
            logger.error(f"无法获取 GitHub 版本信息: {response.text}")
            return jsonify({"error": "无法连接到 GitHub，请稍后再试。"}), 500

        latest_release = response.json()
        latest_version = latest_release.get("tag_name", "").lstrip("v")  # 去掉可能的 'v' 前缀
        release_notes = latest_release.get("body", "无更新说明")

        # 比较版本号
        is_update_available = compare_versions(current_version, latest_version)

        return jsonify({
            "current_version": current_version,
            "latest_version": latest_version,
            "is_update_available": is_update_available,
            "release_notes": release_notes
        })
    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return jsonify({"error": "检查更新失败，请稍后再试。"}), 500

def compare_versions(current, latest):
    """比较版本号，返回是否需要更新"""
    current_parts = list(map(int, current.split('.')))
    latest_parts = list(map(int, latest.split('.')))
    return latest_parts > current_parts

def get_fastest_proxy(original_url):
    """
    测试所有代理站点的响应时间，返回最快的代理地址
    """
    proxy_sites = [
        "https://gitproxy.click/",
        "https://github-proxy.lixxing.top/",
        "https://jiashu.1win.eu.org/",
        "https://gh.llkk.cc/"
    ]
    response_times = {}

    for proxy in proxy_sites:
        proxy_url = proxy + original_url
        try:
            start_time = time.time()
            response = requests.head(proxy_url, timeout=5)  # 使用 HEAD 请求测试响应时间
            response_times[proxy_url] = time.time() - start_time
            if response.status_code != 200:
                response_times[proxy_url] = float('inf')  # 如果响应状态码不是 200，视为不可用
        except requests.RequestException:
            response_times[proxy_url] = float('inf')  # 如果请求失败，视为不可用

    # 找到响应时间最短的代理地址
    fastest_proxy = min(response_times, key=response_times.get)
    logger.info(f"最快的代理地址是: {fastest_proxy}，响应时间: {response_times[fastest_proxy]:.2f} 秒")
    return fastest_proxy

@app.route('/perform_update', methods=['POST'])
@login_required
def perform_update():
    try:
        # 获取当前版本号
        current_version = APP_VERSION

        # 检查是否有更新权限
        if not session.get('user_id'):
            logger.warning("未授权用户尝试执行更新")
            return jsonify({"error": "未授权的操作"}), 403

        logger.info("开始执行更新操作...")

        # 步骤1: 设置 Git 代理加速地址
        original_url = "https://github.com/smysong/mediamaster-v2.git"
        proxy_url = get_fastest_proxy(original_url)
        logger.info(f"正在设置 Git 远程仓库代理地址: {proxy_url}")
        subprocess.run(
            ['git', 'remote', 'set-url', 'origin', proxy_url],
            capture_output=True,
            text=True,
            cwd='/app'
        )

        # 步骤2: 拉取最新代码
        logger.info("正在从 Git 仓库拉取最新代码...")
        result = subprocess.run(
            ['git', 'pull'],
            capture_output=True,
            text=True,
            cwd='/app'
        )

        if result.returncode != 0:
            error_message = f"Git 拉取失败: {result.stderr}"
            logger.error(error_message)
            return jsonify({"error": error_message}), 500

        logger.info(f"Git 拉取成功: {result.stdout}")

        # 步骤3: 安装依赖（如果有新的依赖）
        logger.info("正在安装新依赖...")
        install_result = subprocess.run(
            ['pip', 'install', '-r', 'requirements.txt'],
            capture_output=True,
            text=True,
            cwd='/app'
        )

        if install_result.returncode != 0:
            error_message = f"依赖安装失败: {install_result.stderr}"
            logger.error(error_message)
            return jsonify({"error": error_message}), 500

        logger.info(f"依赖安装成功: {install_result.stdout}")

        # 步骤4: 返回成功消息
        logger.info("执行更新已完成！")
        response = jsonify({
            "message": "更新成功！系统将结束主进程并自动重启。如未自动重启，请手动重启容器。",
            "current_version": current_version
        }), 200

        # 步骤5: 异步查找并结束 python main.py 进程
        def terminate_main_process():
            logger.info("正在查找并结束 python main.py 进程...")
            time.sleep(2)
            target_process_name = "main.py"
            found_process = False

            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # 检查进程是否运行了 main.py
                    if target_process_name in proc.info['cmdline']:
                        logger.info(f"找到目标进程: PID={proc.info['pid']}, CMD={proc.info['cmdline']}")
                        proc.terminate()  # 发送终止信号
                        proc.wait(timeout=5)  # 等待进程结束
                        found_process = True
                        logger.info(f"已成功结束进程: PID={proc.info['pid']}")
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    continue

            if not found_process:
                logger.warning("未找到运行中的 python main.py 进程")

        # 启动一个后台线程来执行终止 main.py 的操作
        threading.Thread(target=terminate_main_process).start()

        # 返回成功消息
        return response

    except Exception as e:
        logger.error(f"执行更新失败: {e}")
        return jsonify({"error": "更新失败，请稍后再试。"}), 500

@app.route('/health_check', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    logger.info("程序已启动")
    # 创建硬链接
    src_dir = '/config/avatars'
    dst_dir = '/app/static/uploads/avatars'
    create_soft_link(src_dir, dst_dir)
    app.run(host='0.0.0.0', port=8888, debug=False)