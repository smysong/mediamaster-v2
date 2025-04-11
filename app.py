import sqlite3
import subprocess
import threading
import bcrypt
from flask import Flask, g, render_template, request, redirect, url_for, jsonify, session, flash, session, Response
from functools import wraps
from werkzeug.exceptions import InternalServerError
from manual_search import MediaDownloader  # 导入 MediaDownloader 类
from datetime import timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from flask import stream_with_context
import os
import time
import requests
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
APP_VERSION = '2.0.2'
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
            return jsonify(success=True, message='登录成功。', redirect_url=url_for('index'))

        logger.warning(f"用户 {username} 登录失败: {error}")
        return jsonify(success=False, message=error)

    return render_template('login.html', version=APP_VERSION)

@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    logger.info(f"用户 {user_id} 登出")
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
        logger.info(f"用户 {user_id} 更新个人资料")
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
            logger.info(f"用户 {user_id} 更新了头像: {avatar_url}")
            updated_avatar = True
        elif not avatar_file:
            logger.info(f"用户 {user_id} 未选择新头像，跳过头像更新")

        # 提交数据库更改
        db.commit()
        logger.info(f"用户 {user_id} 个人资料更新成功")

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
        logger.error(f"用户 {user_id} 更新个人资料失败: {e}")
        return jsonify(success=False, message='更新失败，请稍后再试。'), 500

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    user_id = session['user_id']
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        logger.info(f"用户 {user_id} 尝试修改密码")

        db = get_db()
        user = db.execute('SELECT * FROM USERS WHERE ID = ?', (user_id,)).fetchone()

        hashed_password = user['password']
        if not isinstance(hashed_password, str):
            hashed_password = hashed_password.decode('utf-8')

        if user and bcrypt.checkpw(old_password.encode('utf-8'), hashed_password.encode('utf-8')):
            new_hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            db.execute('UPDATE USERS SET PASSWORD = ? WHERE ID = ?', (new_hashed_password, user_id))
            db.commit()
            logger.info(f"用户 {user_id} 密码修改成功")
            return jsonify(success=True, message='您的密码已成功更新。', redirect_url=url_for('index'))

        logger.warning(f"用户 {user_id} 密码修改失败: 旧密码错误")
        return jsonify(success=False, message='旧密码错误。', redirect_url=url_for('change_password'))

    return render_template('change_password.html', nickname=session.get('nickname'), avatar_url=session.get('avatar_url'), version=APP_VERSION)

@app.errorhandler(InternalServerError)
def handle_500(error):
    logger.error(f"服务器错误: {error}")
    return render_template('500.html'), 500

@app.route('/')
@login_required
def index():
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

        return render_template('index.html', 
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
    user_id = session.get('user_id')

    if not keyword:
        logger.warning(f"用户 {user_id} 搜索电影失败: 缺少关键词")
        return jsonify({'error': '缺少关键词'}), 400

    logger.info(f"用户 {user_id} 搜索电影: 关键词={keyword}, 年份={year}")
    try:
        results = downloader.search_movie(keyword, year)
        logger.info(f"用户 {user_id} 搜索电影成功: 返回结果数量={len(results)}")
        return jsonify(results)
    except Exception as e:
        logger.error(f"用户 {user_id} 搜索电影失败: {e}")
        return jsonify({'error': '搜索失败，请稍后再试。'}), 500

@app.route('/api/search_tv_show', methods=['POST'])
@login_required
def api_search_tv_show():
    data = request.json
    keyword = data.get('keyword')
    year = data.get('year')
    user_id = session.get('user_id')

    if not keyword:
        logger.warning(f"用户 {user_id} 搜索电视剧失败: 缺少关键词")
        return jsonify({'error': '缺少关键词'}), 400

    logger.info(f"用户 {user_id} 搜索电视剧: 关键词={keyword}, 年份={year}")
    try:
        results = downloader.search_tv_show(keyword, year)
        logger.info(f"用户 {user_id} 搜索电视剧成功: 返回结果数量={len(results)}")
        return jsonify(results)
    except Exception as e:
        logger.error(f"用户 {user_id} 搜索电视剧失败: {e}")
        return jsonify({'error': '搜索失败，请稍后再试。'}), 500

@app.route('/api/download_movie', methods=['GET'])
@login_required
def api_download_movie():
    link = request.args.get('link')
    title = request.args.get('title')
    year = request.args.get('year')
    user_id = session.get('user_id')

    if not link or not title or not year:
        logger.warning(f"用户 {user_id} 下载电影失败: 缺少参数")
        return jsonify({'error': '缺少参数'}), 400

    logger.info(f"用户 {user_id} 尝试下载电影: 标题={title}, 年份={year}, 链接={link}")
    try:
        success = downloader.download_movie(link, title, year)
        if success:
            logger.info(f"用户 {user_id} 下载电影成功: 标题={title}, 年份={year}")
            return jsonify({'success': True})
        else:
            logger.warning(f"用户 {user_id} 下载电影失败: 标题={title}, 年份={year}")
            return jsonify({'success': False}), 400
    except Exception as e:
        logger.error(f"用户 {user_id} 下载电影失败: {e}")
        return jsonify({'error': '下载失败，请稍后再试。'}), 500

@app.route('/api/download_tv_show', methods=['GET'])
@login_required
def api_download_tv_show():
    link = request.args.get('link')
    title = request.args.get('title')
    year = request.args.get('year')
    user_id = session.get('user_id')

    if not link or not title or not year:
        logger.warning(f"用户 {user_id} 下载电视剧失败: 缺少参数")
        return jsonify({'error': '缺少参数'}), 400

    logger.info(f"用户 {user_id} 尝试下载电视剧: 标题={title}, 年份={year}, 链接={link}")
    try:
        success = downloader.download_tv_show(link, title, year)
        if success:
            logger.info(f"用户 {user_id} 下载电视剧成功: 标题={title}, 年份={year}")
            return jsonify({'success': True})
        else:
            logger.warning(f"用户 {user_id} 下载电视剧失败: 标题={title}, 年份={year}")
            return jsonify({'success': False}), 400
    except Exception as e:
        logger.error(f"用户 {user_id} 下载电视剧失败: {e}")
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
        "download_mgmt": {"type": "switch", "label": "下载管理"},
        "download_username": {"type": "text", "label": "下载器用户名"},
        "download_password": {"type": "password", "label": "下载器密码"},
        "download_mgmt_url": {"type": "text", "label": "下载管理器内网地址"}
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
    # 从数据库中读取 download_mgmt 和 download_mgmt_url 的配置
    download_mgmt_config = db.execute('SELECT OPTION, VALUE FROM CONFIG WHERE OPTION IN (?, ?)', ('download_mgmt', 'download_mgmt_url')).fetchall()
    
    download_mgmt = None
    download_mgmt_url = None
    
    for config in download_mgmt_config:
        if config['OPTION'] == 'download_mgmt':
            download_mgmt = config['VALUE'] == 'True'
        elif config['OPTION'] == 'download_mgmt_url':
            download_mgmt_url = config['VALUE']
    
    # 如果没有从数据库中读取到 download_mgmt 或 download_mgmt_url，则返回错误或默认页面
    if download_mgmt is None or download_mgmt_url is None:
        flash('配置未找到，请检查数据库中的配置项。', 'error')
        return redirect(url_for('settings_page'))
    
    # 确保 download_mgmt_url 指向本机的代理端点
    proxy_base_url = url_for('proxy_download_mgmt', path='', _external=True)
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    # 将信息传递给模板
    return render_template('download_mgmt.html', nickname=nickname, avatar_url=avatar_url, version=APP_VERSION, download_mgmt=download_mgmt, download_mgmt_url=proxy_base_url)

def get_proxy_url():
    # 获取当前请求的协议
    scheme = request.scheme
    # 从数据库中读取 download_mgmt_url 的配置
    db = get_db()
    download_mgmt_url = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('download_mgmt_url',)).fetchone()
    
    if download_mgmt_url:
        download_mgmt_url = download_mgmt_url['VALUE']
    else:
        return None  # 如果没有配置，返回 None
    
    # 确保 download_mgmt_url 指向本机的代理端点
    proxy_base_url = url_for('proxy_download_mgmt', path='', _external=True)
    
    return download_mgmt_url

@app.route('/proxy/download_mgmt/<path:path>', methods=['GET', 'POST'])
def proxy_download_mgmt(path):
    internal_url = get_proxy_url()
    
    if not internal_url:
        return jsonify({"message": "代理 URL 未配置"}), 400
    
    # 构建完整的内部 URL
    internal_url = f"{internal_url}/{path}"
    
    # 转发请求到内部URL
    response = requests.request(
        method=request.method,
        url=internal_url,
        headers={key: value for key, value in request.headers if key != 'Host'},
        data=request.get_data(),
        cookies=request.cookies,
        allow_redirects=False
    )
    
    # 返回响应
    excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
    headers = [(name, value) for (name, value) in response.raw.headers.items() if name.lower() not in excluded_headers]
    return response.content, response.status_code, headers

if __name__ == '__main__':
    logger.info("程序已启动")
    app.run(host='0.0.0.0', port=8888, debug=False)