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
from datetime import timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from flask import stream_with_context
from transmission_rpc import Client as TransmissionClient
from qbittorrentapi import Client as QbittorrentClient
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Response, stream_with_context
import json
import threading
import os
import time
import logging
import json
import glob
import threading
import uuid
from collections import deque

# 使用线程安全的字典存储下载进度
download_progress_messages = {}
# 使用锁确保线程安全
progress_lock = threading.Lock()

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
        password = request.form['password']
        # 获取记住我选项
        remember_me = request.form.get('remember') == 'on'  # 检查是否勾选了自动登录
        
        logger.info(f"用户 {username} 尝试登录，记住我: {remember_me}")
        
        db = get_db()
        error = None
        user = db.execute('SELECT * FROM USERS WHERE USERNAME = ?', (username,)).fetchone()
        
        if user is None:
            error = '用户名或密码错误'
            logger.warning(f"用户 {username} 登录失败: 用户不存在")
        else:
            # 检查并处理密码字段类型兼容性问题
            stored_password = user['PASSWORD']
            if isinstance(stored_password, str):
                stored_password = stored_password.encode('utf-8')
            elif not isinstance(stored_password, bytes):
                error = '用户数据格式异常，请重置密码！'
                logger.error(f"用户 {username} 登录失败: 用户数据格式异常，请重置密码！")
            else:
                # stored_password 已经是 bytes 类型
                pass
            
            # 如果没有前面的错误，继续验证密码
            if error is None:
                if not bcrypt.checkpw(password.encode('utf-8'), stored_password):
                    error = '用户名或密码错误'
                    logger.warning(f"用户 {username} 登录失败: 密码错误")
        
        if error is None:
            # 登录成功
            session.clear()
            session['user_id'] = user['ID']
            session['username'] = user['USERNAME']
            session['nickname'] = user['NICKNAME']
            session['avatar_url'] = user['AVATAR_URL']
            
            # 根据是否勾选"自动登录"设置session过期时间
            if remember_me:
                # 勾选了自动登录，设置session为30天后过期
                session.permanent = True
                app.permanent_session_lifetime = timedelta(days=30)
                logger.info(f"用户 {username} 登录成功，已启用自动登录(30天)")
            else:
                # 未勾选自动登录，设置为浏览器会话级别（关闭浏览器即失效）
                session.permanent = False
                logger.info(f"用户 {username} 登录成功，未启用自动登录(浏览器会话级别)")

            # 返回JSON响应给前端
            return jsonify({
                'success': True,
                'redirect_url': '/',
                'message': '登录成功'
            })

        # 登录失败返回错误信息
        return jsonify({
            'success': False,
            'message': error
        })

    # GET请求返回登录页面
    return render_template('login.html', version=APP_VERSION)

@app.route('/logout')
def logout():
    username = session.get('username')
    logger.info(f"用户 {username} 登出")
    session.clear()
    return redirect(url_for('login'))

# 配置允许上传的文件类型
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# 检查文件扩展名是否合法
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 更新用户资料路由
@app.route('/api/update_profile', methods=['POST'])
@login_required
def update_profile():
    try:
        user_id = session['user_id']
        nickname = session.get('nickname')
        logger.info(f"用户 {nickname} 更新个人资料")
        db = get_db()

        # 获取表单数据
        username = request.form.get('username')
        nickname_input = request.form.get('nickname')  # 获取昵称输入
        avatar_file = request.files.get('avatar')
        
        updated_avatar = False
        
        # 更新用户名和昵称（如果有提供）
        if username or nickname_input:
            if username and nickname_input:
                # 同时更新用户名和昵称
                db.execute('UPDATE USERS SET USERNAME = ?, NICKNAME = ? WHERE ID = ?', 
                          (username, nickname_input, user_id))
                logger.info(f"用户 {nickname} 更新了用户名和昵称: {username}, {nickname_input}")
            elif username:
                # 只更新用户名
                db.execute('UPDATE USERS SET USERNAME = ? WHERE ID = ?', (username, user_id))
                logger.info(f"用户 {nickname} 更新了用户名: {username}")
            elif nickname_input:
                # 只更新昵称
                db.execute('UPDATE USERS SET NICKNAME = ? WHERE ID = ?', (nickname_input, user_id))
                logger.info(f"用户 {nickname} 更新了昵称: {nickname_input}")
        
        # 更新头像
        if avatar_file and allowed_file(avatar_file.filename):
            upload_folder = 'static/uploads/avatars'
            os.makedirs(upload_folder, exist_ok=True)
            filename = secure_filename(avatar_file.filename)
            file_path = os.path.join(upload_folder, filename)
            avatar_file.save(file_path)
            avatar_url = f"/{upload_folder}/{filename}"
            # 注意：这里使用大写字段名 'AVATAR_URL'
            db.execute('UPDATE USERS SET AVATAR_URL = ? WHERE ID = ?', (avatar_url, user_id))
            logger.info(f"用户 {nickname} 更新了头像: {avatar_url}")
            updated_avatar = True
        elif not avatar_file:
            logger.info(f"用户 {nickname} 未选择新头像，跳过头像更新")

        # 提交数据库更改
        db.commit()
        logger.info(f"用户 {nickname} 个人资料更新成功")

        # 更新会话中的信息
        if username:
            session['username'] = username
        if nickname_input:
            session['nickname'] = nickname_input
            nickname = nickname_input  # 更新本地变量
        if updated_avatar:
            session['avatar_url'] = avatar_url

        return jsonify({"success": True, "message": "个人资料更新成功"})
    except Exception as e:
        logger.error(f"更新个人资料失败: {e}")
        return jsonify({"success": False, "message": "更新失败，请稍后再试"}), 500

# 修改密码路由
@app.route('/api/change_password', methods=['POST'])
@login_required
def change_password():
    try:
        user_id = session['user_id']
        nickname = session.get('nickname')
        logger.info(f"用户 {nickname} 请求修改密码")

        # 获取表单数据
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # 验证输入
        if not old_password or not new_password:
            logger.warning(f"用户 {nickname} 密码修改失败: 缺少必要参数")
            return jsonify(success=False, message='请提供当前密码和新密码。'), 400
            
        # 验证确认密码
        if new_password != confirm_password:
            logger.warning(f"用户 {nickname} 密码修改失败: 新密码和确认密码不一致")
            return jsonify(success=False, message='新密码和确认密码不一致。'), 400

        db = get_db()
        
        # 获取当前用户信息
        user = db.execute('SELECT * FROM USERS WHERE ID = ?', (user_id,)).fetchone()
        if not user:
            logger.error(f"用户 {nickname} 密码修改失败: 用户不存在")
            return jsonify(success=False, message='用户不存在。'), 400

        # 验证当前密码 (使用 bcrypt 验证)
        hashed_password = user['PASSWORD']
        if not isinstance(hashed_password, str):
            hashed_password = hashed_password.decode('utf-8')
            
        if not bcrypt.checkpw(old_password.encode('utf-8'), hashed_password.encode('utf-8')):
            logger.warning(f"用户 {nickname} 密码修改失败: 当前密码错误")
            return jsonify(success=False, message='当前密码错误。'), 400

        # 更新密码 (使用 bcrypt 生成新密码)
        new_hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
        if isinstance(new_hashed_password, bytes):
            new_hashed_password = new_hashed_password.decode('utf-8')
            
        db.execute('UPDATE USERS SET PASSWORD = ? WHERE ID = ?', (new_hashed_password, user_id))
        db.commit()
        
        logger.info(f"用户 {nickname} 密码修改成功")
        return jsonify(success=True, message='密码修改成功！'), 200
        
    except Exception as e:
        logger.error(f"修改密码失败: {e}")
        return jsonify(success=False, message='密码修改失败，请稍后再试。'), 500

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
    username = session.get('username')
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')

    return render_template('dashboard.html', 
                           total_movies=total_movies, 
                           total_tvs=total_tvs, 
                           total_episodes=total_episodes, 
                           nickname=nickname, 
                           username=username, 
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
        "net_io_sent": round(net_io_sent_per_sec, 2),     # 网络上传速率（KB/s）
        "net_io_recv": round(net_io_recv_per_sec, 2),     # 网络下载速率（KB/s）
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
            # 计算运行时长（秒）
            uptime = time.time() - proc.info['create_time']
            
            # 格式化运行时长为天、小时、分钟、秒
            days = int(uptime // (3600 * 24))
            hours = int((uptime % (3600 * 24)) // 3600)
            minutes = int((uptime % 3600) // 60)
            seconds = int(uptime % 60)

            if days > 0:
                uptime_formatted = f"{days}天{hours:02d}小时"
            else:
                uptime_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            
            # 获取命令行参数
            cmdline = proc.info['cmdline']
            
            # 初始化文件名为 None
            file_name = None
            
            # 如果进程名为 'python' 或 'python3'，且 cmdline 不为 None，则尝试获取文件名
            if proc.info['name'] in ['python', 'python3'] and cmdline and len(cmdline) > 1:
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

@app.route('/api/site_status', methods=['GET'])
@login_required
def site_status():
    """
    获取站点状态信息（从文件中读取）
    """
    try:
        # 导入站点测试模块
        import sys
        import os
        import json
        sys.path.append('/app')
        
        # 动态导入站点测试模块
        if 'site_test' in sys.modules:
            import importlib
            importlib.reload(sys.modules['site_test'])
            site_test_module = sys.modules['site_test']
        else:
            import site_test
            site_test_module = site_test
            
        # 创建站点测试实例并获取配置
        tester = site_test_module.SiteTester()
        sites = tester.load_sites_config()
        
        # 读取站点启用状态
        db = get_db()
        enabled_sites = {}
        for site_name in sites.keys():
            option_name = f"{site_name.lower()}_enabled"
            try:
                result = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', (option_name,)).fetchone()
                enabled_sites[site_name] = result['VALUE'] == 'True' if result else False
            except Exception as e:
                logger.error(f"读取站点 {site_name} 启用状态失败: {e}")
                enabled_sites[site_name] = False
        
        # 读取站点状态文件
        status_file_path = '/tmp/site_status.json'
        site_status_data = {}
        last_checked = None
        
        if os.path.exists(status_file_path):
            try:
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    site_status_data = data.get('status', {})
                    last_checked = data.get('last_checked')
            except json.JSONDecodeError as e:
                logger.error(f"解析站点状态文件失败: {e}")
            except Exception as e:
                logger.error(f"读取站点状态文件失败: {e}")
        else:
            logger.warning("站点状态文件不存在")
        
        # 返回站点信息
        site_info = []
        for site_name, site_config in sites.items():
            site_info.append({
                'name': site_name,
                'url': site_config['base_url'],
                'keyword': site_config['keyword'],
                'enabled': enabled_sites.get(site_name, False)
            })
        
        return jsonify({
            'sites': site_info,
            'last_checked': last_checked,
            'status': site_status_data
        })
    except Exception as e:
        logger.error(f"获取站点状态失败: {e}")
        return jsonify({'error': '获取站点状态失败'}), 500

@app.route('/api/check_site_status', methods=['POST'])
@login_required
def check_site_status():
    """
    手动检查站点状态并更新状态文件
    """
    try:
        import sys
        import os
        import json
        sys.path.append('/app')
        
        # 动态导入站点测试模块
        if 'site_test' in sys.modules:
            import importlib
            importlib.reload(sys.modules['site_test'])
            site_test_module = sys.modules['site_test']
        else:
            import site_test
            site_test_module = site_test
            
        # 运行站点测试
        tester = site_test_module.SiteTester()
        results = tester.run_tests()
        
        # 保存结果到文件
        status_data = {
            'status': results,
            'last_checked': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        with open('/tmp/site_status.json', 'w', encoding='utf-8') as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
        
        return jsonify({
            'status': results,
            'last_checked': status_data['last_checked']
        })
    except Exception as e:
        logger.error(f"检查站点状态失败: {e}")
        return jsonify({'error': '检查站点状态失败'}), 500

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
                
                # 兼容处理 episodes 字段（可能是整数或字符串）
                episodes = tv['episodes']
                if isinstance(episodes, int):
                    episodes = str(episodes)

                # 解析 episodes 字符串，计算总集数
                episodes_list = episodes.split(',')
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
    # 从数据库中读取 tmdb_api_key
    tmdb_api_key = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('tmdb_api_key',)).fetchone()
    tmdb_api_key = tmdb_api_key['VALUE'] if tmdb_api_key else None
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('subscriptions.html', 
                         miss_movies=miss_movies, 
                         miss_tvs=miss_tvs, 
                         tmdb_api_key=tmdb_api_key,
                         nickname=nickname, 
                         avatar_url=avatar_url, 
                         version=APP_VERSION)

# 手动添加订阅
@app.route('/add_subscription', methods=['POST'])
@login_required
def add_subscription():
    try:
        # 获取请求数据
        data = request.json
        subscription_type = data.get('type')
        title = data.get('title')
        year = data.get('year')
        season = data.get('season', 1)  # 默认第一季
        start_episode = data.get('start_episode')
        end_episode = data.get('end_episode')

        # 检查必要字段
        if not subscription_type or not title or not year:
            return jsonify({"success": False, "message": "缺少必要的订阅信息"}), 400

        db = get_db()

        if subscription_type == 'tv':  # 电视剧订阅
            # 验证剧集信息
            if start_episode is None or end_episode is None:
                return jsonify({"success": False, "message": "电视剧订阅需要提供起始集和结束集"}), 400
            
            try:
                start_episode = int(start_episode)
                end_episode = int(end_episode)
                season = int(season)
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "季、起始集和结束集必须是数字"}), 400
                
            if start_episode <= 0 or end_episode <= 0 or start_episode > end_episode:
                return jsonify({"success": False, "message": "起始集和结束集必须是正整数，且起始集不能大于结束集"}), 400

            # 生成缺失的集数字符串，例如 "1,2,3,...,episodes"
            missing_episodes = ','.join(map(str, range(start_episode, end_episode + 1)))

            # 生成手动订阅的douban_id
            # 获取当前最大的manual编号
            max_id_row = db.execute(
                "SELECT MAX(CAST(SUBSTR(douban_id, 8) AS INTEGER)) as max_id FROM MISS_TVS WHERE douban_id LIKE 'manual-%'"
            ).fetchone()
            
            max_id = max_id_row['max_id'] if max_id_row['max_id'] else 0
            new_douban_id = f"manual-{max_id + 1}"

            # 检查是否已存在相同的订阅
            existing_tv = db.execute(
                'SELECT * FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                (title, year, season)
            ).fetchone()

            if existing_tv:
                return jsonify({"success": False, "message": "该电视剧订阅已存在"}), 400

            # 插入电视剧订阅
            db.execute(
                'INSERT INTO MISS_TVS (douban_id, title, year, season, missing_episodes) VALUES (?, ?, ?, ?, ?)',
                (new_douban_id, title, year, season, missing_episodes)
            )
            db.commit()
            logger.info(f"用户添加电视剧订阅: {title} ({year}) 季{season} 集{start_episode}-{end_episode} DOUBAN_ID: {new_douban_id}")
            return jsonify({"success": True, "message": "电视剧订阅添加成功"})

        elif subscription_type == 'movie':  # 电影订阅
            # 生成手动订阅的douban_id
            # 获取当前最大的manual编号
            max_id_row = db.execute(
                "SELECT MAX(CAST(SUBSTR(douban_id, 8) AS INTEGER)) as max_id FROM MISS_MOVIES WHERE douban_id LIKE 'manual%'"
            ).fetchone()
            
            max_id = max_id_row['max_id'] if max_id_row['max_id'] else 0
            new_douban_id = f"manual{max_id + 1}"

            # 检查是否已存在相同的订阅
            existing_movie = db.execute(
                'SELECT * FROM MISS_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            ).fetchone()

            if existing_movie:
                return jsonify({"success": False, "message": "该电影订阅已存在"}), 400

            # 插入电影订阅
            db.execute(
                'INSERT INTO MISS_MOVIES (douban_id, title, year) VALUES (?, ?, ?)',
                (new_douban_id, title, year)
            )
            db.commit()
            logger.info(f"用户添加电影订阅: {title} ({year}) DOUBAN_ID: {new_douban_id}")
            return jsonify({"success": True, "message": "电影订阅添加成功"})

        else:
            return jsonify({"success": False, "message": "无效的订阅类型"}), 400

    except Exception as e:
        logger.error(f"添加订阅失败: {e}")
        return jsonify({"success": False, "message": "添加订阅失败，请稍后再试"}), 500

# 取消热门推荐中的订阅
@app.route('/cancel_subscription', methods=['POST'])
@login_required
def cancel_subscription():
    try:
        # 获取请求数据
        data = request.json
        title = data.get('title')
        year = data.get('year')
        season = data.get('season')
        media_type = data.get('mediaType')

        # 检查必要字段
        if not title or not year or not media_type:
            return jsonify({"success": False, "message": "缺少必要的参数"}), 400

        db = get_db()

        if media_type == 'tv':  # 电视剧取消订阅
            # 检查是否存在该订阅
            existing_tv = db.execute(
                'SELECT * FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                (title, year, season)
            ).fetchone()

            if not existing_tv:
                return jsonify({"success": False, "message": "未找到该电视剧订阅"}), 404

            # 删除订阅
            db.execute(
                'DELETE FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                (title, year, season)
            )
            db.commit()
            logger.info(f"用户取消电视剧订阅: {title} ({year}) 季{season}")
            return jsonify({"success": True, "message": "电视剧订阅已取消"})

        elif media_type == 'movie':  # 电影取消订阅
            # 检查是否存在该订阅
            existing_movie = db.execute(
                'SELECT * FROM MISS_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            ).fetchone()

            if not existing_movie:
                return jsonify({"success": False, "message": "未找到该电影订阅"}), 404

            # 删除订阅
            db.execute(
                'DELETE FROM MISS_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            )
            db.commit()
            logger.info(f"用户取消电影订阅: {title} ({year})")
            return jsonify({"success": True, "message": "电影订阅已取消"})

        else:
            return jsonify({"success": False, "message": "无效的媒体类型"}), 400

    except Exception as e:
        logger.error(f"取消订阅失败: {e}")
        return jsonify({"success": False, "message": "取消订阅失败，请稍后再试"}), 500

# 从热门推荐中添加订阅
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

            # 检查是否已存在相同的订阅（标题、年份和季数的组合）
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

# 检查热门推荐中的订阅状态（是否已订阅或已入库）
@app.route('/check_subscriptions', methods=['POST'])
@login_required
def check_subscriptions():
    try:
        data = request.json
        title = data.get('title')
        year = data.get('year')
        season = data.get('season')  # 如果是电视剧，获取季编号

        db = get_db()

        # 检查是否已订阅
        if season:  # 检查电视剧订阅（特定季）
            existing_tv = db.execute(
                'SELECT * FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                (title, year, season)
            ).fetchone()
            if existing_tv:
                return jsonify({"subscribed": True, "status": "subscribed"})

            # 检查是否已入库（特定季）
            existing_tv_in_library = db.execute(
                '''
                SELECT t1.id FROM LIB_TVS AS t1
                JOIN LIB_TV_SEASONS AS t2 ON t1.id = t2.tv_id
                WHERE t1.title = ? AND t1.year = ? AND t2.season = ?
                ''',
                (title, year, season)
            ).fetchone()
            if existing_tv_in_library:
                return jsonify({"subscribed": True, "status": "in_library"})
        else:  # 检查电影订阅或电视剧整体订阅
            # 检查电影订阅
            existing_movie = db.execute(
                'SELECT * FROM MISS_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            ).fetchone()
            if existing_movie:
                return jsonify({"subscribed": True, "status": "subscribed"})

            # 检查是否已入库（电影）
            existing_movie_in_library = db.execute(
                'SELECT id FROM LIB_MOVIES WHERE title = ? AND year = ?',
                (title, year)
            ).fetchone()
            if existing_movie_in_library:
                return jsonify({"subscribed": True, "status": "in_library"})

        return jsonify({"subscribed": False, "status": "not_found"})
    except Exception as e:
        logger.error(f"检查订阅状态失败: {e}")
        return jsonify({"subscribed": False, "error": "检查失败"}), 500

@app.route('/edit_subscription/<type>/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_subscription(type, id):
    db = get_db()
    if type == 'movie':
        subscription = db.execute('SELECT * FROM MISS_MOVIES WHERE id = ?', (id,)).fetchone()
    elif type == 'tv':
        subscription = db.execute('SELECT * FROM MISS_TVS WHERE id = ?', (id,)).fetchone()
    else:
        return jsonify(success=False, message="Invalid subscription type"), 400

    if request.method == 'POST':
        title = request.form['title']
        year = request.form.get('year')
        season = request.form.get('season')
        missing_episodes = request.form.get('missing_episodes')

        try:
            if type == 'movie':
                db.execute('UPDATE MISS_MOVIES SET title = ?, year = ? WHERE id = ?', (title, year, id))
            elif type == 'tv':
                db.execute('UPDATE MISS_TVS SET title = ?, season = ?, missing_episodes = ? WHERE id = ?', 
                          (title, season, missing_episodes, id))
            db.commit()
            logger.info(f"用户更新订阅: {type} ID={id}")
            return jsonify(success=True, message="订阅更新成功")
        except Exception as e:
            db.rollback()
            logger.error(f"更新订阅失败: {e}")
            return jsonify(success=False, message="更新失败，请稍后再试"), 500

    # GET 请求时返回 JSON 数据
    if subscription:
        return jsonify(dict(subscription))
    else:
        return jsonify(success=False, message="未找到订阅"), 404

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

# 获取豆瓣想看数据的JSON接口
@app.route('/douban_subscriptions_json')
@login_required
def douban_subscriptions_json():
    db = get_db()
    rss_movies = db.execute('SELECT * FROM RSS_MOVIES').fetchall()
    rss_tvs = db.execute('SELECT * FROM RSS_TVS').fetchall()
    
    # 将Row对象转换为字典列表
    rss_movies_dict = [dict(row) for row in rss_movies]
    rss_tvs_dict = [dict(row) for row in rss_tvs]
    
    return jsonify({
        "rss_movies": rss_movies_dict,
        "rss_tvs": rss_tvs_dict
    })

# 获取剧集关联列表的JSON接口
@app.route('/tv_alias_list_json')
@login_required
def tv_alias_list_json():
    try:
        db = get_db()
        alias_list = db.execute('SELECT * FROM LIB_TV_ALIAS ORDER BY id DESC').fetchall()
        # 将Row对象转换为字典列表
        alias_list_dict = [dict(row) for row in alias_list]
        return jsonify({"alias_list": alias_list_dict})
    except Exception as e:
        logger.error(f"获取剧集关联列表失败: {e}")
        return jsonify({"error": "获取剧集关联列表失败"}), 500

# 获取单个剧集关联信息的JSON接口
@app.route('/tv_alias_edit_json/<int:alias_id>')
@login_required
def tv_alias_edit_json(alias_id):
    try:
        db = get_db()
        alias = db.execute('SELECT * FROM LIB_TV_ALIAS WHERE id = ?', (alias_id,)).fetchone()
        if alias:
            return jsonify({"alias": dict(alias)})
        else:
            return jsonify({"error": "未找到该关联"}), 404
    except Exception as e:
        logger.error(f"获取剧集关联信息失败: {e}")
        return jsonify({"error": "获取剧集关联信息失败"}), 500

# 添加剧集关联的API接口
@app.route('/tv_alias_add', methods=['POST'])
@login_required
def tv_alias_add_api():
    try:
        data = request.json
        alias = data.get('alias', '').strip()
        target_title = data.get('target_title', '').strip()
        target_season = data.get('target_season', None)
        
        if not alias or not target_title:
            return jsonify({"success": False, "message": "别名和目标名称不能为空"}), 400
            
        db = get_db()
        try:
            db.execute('INSERT INTO LIB_TV_ALIAS (ALIAS, TARGET_TITLE, TARGET_SEASON) VALUES (?, ?, ?)', 
                      (alias, target_title, target_season))
            db.commit()
            return jsonify({"success": True, "message": "添加成功"})
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "message": "该别名已存在"}), 400
    except Exception as e:
        logger.error(f"添加剧集关联失败: {e}")
        return jsonify({"success": False, "message": "添加失败，请稍后再试"}), 500

# 编辑剧集关联的API接口
@app.route('/tv_alias_edit/<int:alias_id>', methods=['POST'])
@login_required
def tv_alias_edit_api(alias_id):
    try:
        data = request.json
        alias = data.get('alias', '').strip()
        target_title = data.get('target_title', '').strip()
        target_season = data.get('target_season', None)
        
        if not alias or not target_title:
            return jsonify({"success": False, "message": "别名和目标名称不能为空"}), 400
            
        db = get_db()
        existing_alias = db.execute('SELECT * FROM LIB_TV_ALIAS WHERE id = ?', (alias_id,)).fetchone()
        if not existing_alias:
            return jsonify({"success": False, "message": "未找到该关联"}), 404
            
        try:
            db.execute('UPDATE LIB_TV_ALIAS SET ALIAS = ?, TARGET_TITLE = ?, TARGET_SEASON = ? WHERE id = ?', 
                      (alias, target_title, target_season, alias_id))
            db.commit()
            return jsonify({"success": True, "message": "更新成功"})
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "message": "该别名已存在"}), 400
    except Exception as e:
        logger.error(f"更新剧集关联失败: {e}")
        return jsonify({"success": False, "message": "更新失败，请稍后再试"}), 500

# 删除剧集关联的API接口
@app.route('/tv_alias_delete/<int:alias_id>', methods=['POST'])
@login_required
def tv_alias_delete_api(alias_id):
    try:
        db = get_db()
        existing_alias = db.execute('SELECT * FROM LIB_TV_ALIAS WHERE id = ?', (alias_id,)).fetchone()
        if not existing_alias:
            return jsonify({"success": False, "message": "未找到该关联"}), 404
            
        db.execute('DELETE FROM LIB_TV_ALIAS WHERE id = ?', (alias_id,))
        db.commit()
        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        logger.error(f"删除剧集关联失败: {e}")
        return jsonify({"success": False, "message": "删除失败，请稍后再试"}), 500

@app.route('/service_control')
@login_required
def service_control():
    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')
    return render_template('service_control.html', nickname=nickname, avatar_url=avatar_url, version=APP_VERSION)

@app.route('/run_service', methods=['POST'])
@login_required
def run_service():
    data = request.get_json()
    service = data.get('service')
    try:
        logger.info(f"尝试启动服务: {service}")
        log_file_path = f'/tmp/log/{service}.log'
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)  # 确保日志目录存在
        with open(log_file_path, 'w', encoding='utf-8') as log_file:
            process = subprocess.Popen(['python3', f'/app/{service}.py'], stdout=log_file, stderr=log_file)
            pid = process.pid
            running_services[service] = pid
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
    db = get_db()
    # 从数据库中读取 tmdb_api_key
    tmdb_api_key = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('tmdb_api_key',)).fetchone()
    tmdb_api_key = tmdb_api_key['VALUE'] if tmdb_api_key else None
    logger.info(f"用户 {nickname} 访问手动搜索页面")
    return render_template('manual_search.html', nickname=nickname, avatar_url=avatar_url, version=APP_VERSION, tmdb_api_key=tmdb_api_key)

@app.route('/api/search_media', methods=['POST'])
@login_required
def api_search_media():
    """
    统一资源搜索接口，支持电影和电视剧的搜索。
    """
    data = request.json
    media_type = data.get('type')  # 'movie' 或 'tv'
    title = data.get('title')
    year = data.get('year')
    season = data.get('season')  # 获取季数参数
    force_refresh = data.get('force_refresh', False)  # 是否强制刷新
    nickname = session.get('nickname')

    if not media_type or not title or not year:
        logger.warning(f"用户 {nickname} 搜索资源失败: 缺少参数")
        return jsonify({'error': '缺少参数'}), 400

    logger.info(f"用户 {nickname} 搜索资源: 类型={media_type}, 标题={title}, 年份={year}" + (f", 季数={season}" if season else "") + (f", 强制刷新={force_refresh}" if force_refresh else ""))
    
    def generate():
        try:
            # 开始搜索
            yield f"data: {json.dumps({'status': 'start', 'message': '开始搜索资源'})}\n\n"
            
            # 检查缓存结果（除非强制刷新）
            results_dir = "/tmp/index/"
            current_time = time.time()
            time_threshold = 30 * 60  # 30分钟 = 1800秒
            
            if not force_refresh:
                # 根据媒体类型使用不同的文件匹配模式
                if media_type == "tv":
                    # 电视剧文件命名格式
                    if season:
                        result_pattern = os.path.join(results_dir, f"{title}-S{season}*{year}*.json")
                    else:
                        result_pattern = os.path.join(results_dir, f"{title}-S*{year}*.json")
                else:
                    # 电影文件命名格式
                    result_pattern = os.path.join(results_dir, f"{title}-*{year}*.json")
                
                existing_result_files = glob.glob(result_pattern)
                
                # 过滤文件
                filtered_files = []
                for file in existing_result_files:
                    filename = os.path.basename(file)
                    if media_type == "movie":
                        # 排除电视剧格式（包含-S数字的文件）
                        if not re.search(rf"{re.escape(title)}-S\d+.*{year}", filename):
                            filtered_files.append(file)
                    elif media_type == "tv" and season:
                        # 只保留指定season的文件
                        if re.search(rf"{re.escape(title)}-S{season}.*{year}", filename):
                            filtered_files.append(file)
                    else:
                        filtered_files.append(file)
                
                valid_results = []
                for result_file in filtered_files:
                    try:
                        # 检查文件修改时间
                        file_mtime = os.path.getmtime(result_file)
                        if current_time - file_mtime <= time_threshold:
                            valid_results.append(result_file)
                    except OSError:
                        # 如果无法获取文件信息，跳过该文件
                        continue
                
                # 如果存在有效结果文件，直接读取并返回
                if valid_results:
                    yield f"data: {json.dumps({'status': 'cache_found', 'message': f'发现 {len(valid_results)} 个缓存结果'})}\n\n"
                    
                    all_results = {}
                    for i, result_file in enumerate(valid_results):
                        try:
                            filename = os.path.basename(result_file)
                            site = None
                            
                            # 基础搜索脚本配置（用于提取站点信息）
                            search_scripts = [
                                {"site": "BTHD"},
                                {"site": "HDTV"},
                                {"site": "BTYS"},
                                {"site": "BT0"},
                                {"site": "GY"}
                            ]
                            
                            for script_info in search_scripts:
                                if script_info["site"] in filename:
                                    site = script_info["site"]
                                    break
                            
                            if not site:
                                continue
                            
                            with open(result_file, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                
                                # 按站点分类结果
                                if site not in all_results:
                                    all_results[site] = []

                                # 提取需要的字段
                                for resolution_key in ["首选分辨率", "备选分辨率", "其他分辨率"]:
                                    if resolution_key in data:
                                        if media_type == "tv":
                                            # 针对 电视节目 数据
                                            for category_key in ["单集", "集数范围", "全集"]:
                                                if category_key in data[resolution_key]:
                                                    for item in data[resolution_key][category_key]:
                                                        result_item = {
                                                            "title": item.get("title"),
                                                            "size": item.get("size"),
                                                            "link": item.get("link"),
                                                            "resolution": item.get("resolution")
                                                        }
                                                        # 添加热度数据（如果存在）
                                                        if "popularity" in item:
                                                            result_item["popularity"] = item.get("popularity")
                                                        all_results[site].append(result_item)
                                        else:
                                            # 针对 电影 数据
                                            for item in data[resolution_key]:
                                                result_item = {
                                                    "title": item.get("title"),
                                                    "size": item.get("size"),
                                                    "link": item.get("link"),
                                                    "resolution": item.get("resolution")
                                                }
                                                # 添加热度数据（如果存在）
                                                if "popularity" in item:
                                                    result_item["popularity"] = item.get("popularity")
                                                all_results[site].append(result_item)
                            
                            # 发送单个站点的结果
                            yield f"data: {json.dumps({'status': 'result', 'site': site, 'data': all_results[site]})}\n\n"
                            
                        except Exception as e:
                            logger.error(f"读取缓存搜索结果失败: {result_file}, 错误: {e}")
                    
                    yield f"data: {json.dumps({'status': 'complete', 'message': '缓存结果加载完成'})}\n\n"
                    return
            
            # 没有缓存结果或强制刷新，执行实时搜索
            if force_refresh:
                yield f"data: {json.dumps({'status': 'no_cache', 'message': '强制刷新，开始实时搜索'})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'no_cache', 'message': '未找到缓存结果，开始实时搜索'})}\n\n"
            
            # 基础搜索脚本配置
            search_scripts = [
                {
                    "script": "python movie_bthd.py --manual --title \"{title}\" --year {year}",
                    "type": "movie",
                    "site": "BTHD"
                },
                {
                    "script": "python tvshow_hdtv.py --manual --title \"{title}\" --year {year}",
                    "type": "tv",
                    "site": "HDTV"
                },
                {
                    "script": "python movie_tvshow_btys.py --manual --type {type} --title \"{title}\" --year {year}",
                    "type": "both",
                    "site": "BTYS"
                },
                {
                    "script": "python movie_tvshow_bt0.py --manual --type {type} --title \"{title}\" --year {year}",
                    "type": "both",
                    "site": "BT0"
                },
                {
                    "script": "python movie_tvshow_gy.py --manual --type {type} --title \"{title}\" --year {year}",
                    "type": "both",
                    "site": "GY"
                }
            ]

            # 为电视剧脚本添加season参数（仅当season存在时）
            for script_info in search_scripts:
                if (script_info["type"] == "tv" or script_info["type"] == "both") and media_type == "tv" and season is not None:
                    script_info["script"] += " --season {season}"

            all_results = {}
            
            # 定义执行单个脚本的函数
            def execute_script(script_info, instance_id):
                # 格式化脚本命令
                if media_type == "tv" and season is not None:
                    script_command = script_info["script"].format(
                        type=media_type,
                        title=title,
                        year=year,
                        season=season
                    )
                else:
                    script_command = script_info["script"].format(
                        type=media_type,
                        title=title,
                        year=year
                    )
                
                # 添加instance_id参数
                script_command += f" --instance-id {instance_id}"
                
                try:
                    # 执行搜索脚本
                    subprocess.run(script_command, shell=True, check=True)
                    logger.info(f"成功执行脚本: {script_command}")
                    return script_info, True
                except subprocess.CalledProcessError as e:
                    logger.error(f"执行脚本失败: {script_command}, 错误: {e}")
                    return script_info, False
            
            # 使用线程池并行执行脚本
            with ThreadPoolExecutor(max_workers=5) as executor:
                # 提交所有符合条件的脚本任务
                future_to_script = {}
                for i, script_info in enumerate(search_scripts):
                    if script_info["type"] == "both" or script_info["type"] == media_type:
                        # 为每个脚本生成固定的instance_id
                        instance_id = f"manual_{i}"
                        future = executor.submit(execute_script, script_info, instance_id)
                        future_to_script[future] = script_info
                
                # 等待所有任务完成并处理结果
                completed_count = 0
                total_scripts = len(future_to_script)
                
                for future in as_completed(future_to_script):
                    script_info, success = future.result()
                    completed_count += 1
                    
                    if not success:
                        message = f'站点 {script_info["site"]} 搜索失败 ({completed_count}/{total_scripts})'
                        yield f"data: {json.dumps({'status': 'progress', 'message': message})}\n\n"
                        continue
                    
                    # 使用通配符匹配结果文件
                    if media_type == "tv":
                        # 电视剧结果文件命名格式
                        if season:
                            result_pattern = os.path.join(results_dir, f"{title}-S{season}*{year}*{script_info['site']}*.json")
                        else:
                            result_pattern = os.path.join(results_dir, f"{title}-S*{year}*{script_info['site']}*.json")
                    else:
                        # 电影结果文件命名格式
                        result_pattern = os.path.join(results_dir, f"{title}-*{year}*{script_info['site']}*.json")
                    
                    result_files = glob.glob(result_pattern)

                    # 过滤文件
                    filtered_files = []
                    for file in result_files:
                        filename = os.path.basename(file)
                        if media_type == "movie":
                            # 排除电视剧格式（包含-S数字的文件）
                            if not re.search(rf"{re.escape(title)}-S\d+.*{year}.*{script_info['site']}", filename):
                                filtered_files.append(file)
                        elif media_type == "tv" and season:
                            # 只保留指定season的文件
                            if re.search(rf"{re.escape(title)}-S{season}.*{year}.*{script_info['site']}", filename):
                                filtered_files.append(file)
                        else:
                            filtered_files.append(file)
                    
                    result_files = filtered_files

                    if not result_files:
                        message = f'站点 {script_info["site"]} 未找到结果 ({completed_count}/{total_scripts})'
                        yield f"data: {json.dumps({'status': 'progress', 'message': message})}\n\n"
                        continue

                    for result_file in result_files:
                        try:
                            with open(result_file, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                
                                # 按站点分类结果
                                site = script_info["site"]
                                if site not in all_results:
                                    all_results[site] = []

                                # 提取需要的字段
                                for resolution_key in ["首选分辨率", "备选分辨率", "其他分辨率"]:
                                    if resolution_key in data:
                                        if media_type == "tv":
                                            # 针对 电视节目 数据
                                            for category_key in ["单集", "集数范围", "全集"]:
                                                if category_key in data[resolution_key]:
                                                    for item in data[resolution_key][category_key]:
                                                        result_item = {
                                                            "title": item.get("title"),
                                                            "size": item.get("size"),
                                                            "link": item.get("link"),
                                                            "resolution": item.get("resolution")
                                                        }
                                                        # 添加热度数据（如果存在）
                                                        if "popularity" in item:
                                                            result_item["popularity"] = item.get("popularity")
                                                        all_results[site].append(result_item)
                                        else:
                                            # 针对 电影 数据
                                            for item in data[resolution_key]:
                                                result_item = {
                                                    "title": item.get("title"),
                                                    "size": item.get("size"),
                                                    "link": item.get("link"),
                                                    "resolution": item.get("resolution")
                                                }
                                                # 添加热度数据（如果存在）
                                                if "popularity" in item:
                                                    result_item["popularity"] = item.get("popularity")
                                                all_results[site].append(result_item)
                                
                                # 发送单个站点的结果
                                yield f"data: {json.dumps({'status': 'result', 'site': site, 'data': all_results[site]})}\n\n"
                                
                        except Exception as e:
                            logger.error(f"读取搜索结果失败: {result_file}, 错误: {e}")
                    
                    message = f'完成站点 {script_info["site"]} 搜索 ({completed_count}/{total_scripts})'
                    yield f"data: {json.dumps({'status': 'progress', 'message': message})}\n\n"
            
            yield f"data: {json.dumps({'status': 'complete', 'message': '所有搜索完成'})}\n\n"
            
        except Exception as e:
            logger.error(f"用户 {nickname} 搜索资源失败: {e}")
            yield f"data: {json.dumps({'status': 'error', 'message': '搜索过程中发生错误'})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/plain')

# 资源下载进度API
@app.route('/api/download_progress/<task_id>')
@login_required
def download_progress(task_id):
    """
    获取指定资源下载任务的下载进度信息
    """
    global download_progress_messages
    try:
        with progress_lock:
            # 返回指定任务的进度信息
            if task_id in download_progress_messages and download_progress_messages[task_id]:
                # 转换 deque 为 list
                messages_list = list(download_progress_messages[task_id])
                return jsonify({"messages": messages_list})
            else:
                return jsonify({"messages": ["等待任务开始..."]})
    except Exception as e:
        logger.error(f"获取下载进度失败: {e}")
        return jsonify({"error": "获取进度失败"}), 500

# 下载资源API
@app.route('/api/download_resource', methods=['POST'])
@login_required
def download_resource():
    """
    接收前端选择的资源数据，并调用 downloader.py 脚本进行下载。
    """
    global download_progress_messages
    
    try:
        # 获取请求数据
        data = request.json
        site = data.get('site')  # 资源站点，例如 "BT0"
        title = data.get('title')  # 资源标题
        link = data.get('link')  # 资源下载链接

        # 检查必要参数
        if not site or not title or not link:
            logger.warning("下载资源失败: 缺少必要参数")
            return jsonify({"error": "缺少必要参数"}), 400

        # 生成任务ID
        task_id = str(uuid.uuid4())

        # 初始化该任务的进度消息队列
        with progress_lock:
            if task_id not in download_progress_messages:
                download_progress_messages[task_id] = deque(maxlen=100)
            download_progress_messages[task_id].append(f"开始种子下载任务: {title}")

        # 构建下载命令（使用列表形式避免shell解析问题，并确保所有参数都是字符串）
        command = [
            'python', 'downloader.py',
            '--site', str(site),
            '--title', str(title),
            '--link', str(link)
        ]
        
        logger.info(f"执行下载命令: {' '.join(command)}")

        # 异步执行下载任务
        def run_download():
            try:
                with progress_lock:
                    if task_id in download_progress_messages:
                        download_progress_messages[task_id].append(f"执行命令: {' '.join(command)}")

                # 使用 subprocess.Popen 异步执行，并实时获取输出
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # 将stderr重定向到stdout，统一处理输出
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # 实时读取标准输出（包括错误信息）
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        with progress_lock:
                            if task_id in download_progress_messages:
                                # 直接添加输出内容
                                download_progress_messages[task_id].append(output.strip())

                process.poll()
                
            except Exception as e:
                logger.error(f"执行下载任务失败: {e}")
                error_msg = f"下载出错: {str(e)}"
                with progress_lock:
                    if task_id in download_progress_messages:
                        download_progress_messages[task_id].append(error_msg)
        
        # 在后台线程中执行下载任务
        thread = threading.Thread(target=run_download)
        thread.daemon = True
        thread.start()
        
        # 立即返回任务ID给前端
        return jsonify({
            "message": "开始后台执行下载",
            "task_id": task_id
        }), 200
            
    except Exception as e:
        logger.error(f"添加任务失败: {e}")
        return jsonify({"error": str(e)}), 500

GROUP_MAPPING = {
    "定时任务": {
        "run_interval_hours": {"type": "text", "label": "自动化流程间隔"}
    },
    "消息通知": {
        "notification": {"type": "switch", "label": "消息通知"},
        "notification_api_key": {"type": "password", "label": "Bark API密钥"},
    },
    "媒体添加时间": {
        "dateadded": {"type": "switch", "label": "使用影片发行日期"}
    },
    "媒体元数据刮削": {
        "scrape_metadata": {"type": "switch", "label": "刮削媒体元数据"},
    },
    "中文演职人员": {
        "actor_nfo": {"type": "switch", "label": "演职人员汉化"},
        "nfo_exclude_dirs": {"type": "text", "label": "排除目录"},
        "nfo_excluded_filenames": {"type": "text", "label": "排除文件名"},
        "nfo_excluded_subdir_keywords": {"type": "text", "label": "排除关键字"}
    },
    "媒体库目录": {
        "media_dir": {"type": "text", "label": "主目录"},
        "movies_path": {"type": "text", "label": "电影目录"},
        "episodes_path": {"type": "text", "label": "电视剧目录"},
        "unknown_path": {"type": "text", "label": "未识别目录"}
    },
    "资源下载设置": {
        "download_dir": {"type": "text", "label": "下载目录"},
        "download_action": {"type": "select", "label": "下载文件转移方式", "options": ["移动", "复制", "软链接", "硬链接"]},
        "download_excluded_filenames": {"type": "text", "label": "下载转移排除的文件名"},
        "preferred_resolution": {"type": "text", "label": "资源下载首选分辨率"},
        "fallback_resolution": {"type": "text", "label": "资源下载备选分辨率"},
        "resources_exclude_keywords": {"type": "text", "label": "资源搜索排除关键词"},
        "resources_prefer_keywords": {"type": "text", "label": "资源下载偏好关键词"}
    },
    "豆瓣设置": {
        "douban_api_key": {"type": "password", "label": "豆瓣API密钥"},
        "douban_cookie": {"type": "text", "label": "豆瓣COOKIE"},
        "douban_user_ids": {"type": "text", "label": "豆瓣订阅用户ID"},
        "douban_rss_url": {"type": "text", "label": "豆瓣订阅地址"}
    },
    "TMDB接口": {
        "tmdb_base_url": {"type": "text", "label": "TMDB API接口地址"},
        "tmdb_api_key": {"type": "password", "label": "TMDB API密钥"}
    },
    "OCR接口": {
        "ocr_api_key": {"type": "password", "label": "OCR API密钥"}
    },
    "下载器管理": {
        "download_mgmt": {"type": "switch", "label": "下载器管理"},
        "download_type": {"type": "downloader", "label": "下载器", "options": ["transmission", "qbittorrent", "xunlei"]},
        "download_username": {"type": "text", "label": "下载器用户名"},
        "download_password": {"type": "password", "label": "下载器密码"},
        "download_host": {"type": "text", "label": "下载器地址"},
        "download_port": {"type": "text", "label": "下载器端口"},
        "xunlei_device_name": {"type": "text", "label": "迅雷设备名称"},
        "xunlei_dir": {"type": "text", "label": "迅雷下载目录"}
    },
    "站点索引开关": {
        "bthd_enabled": {"type": "switch", "label": "高清影视之家"},
        "hdtv_enabled": {"type": "switch", "label": "高清剧集网"},
        "btys_enabled": {"type": "switch", "label": "BT影视"},
        "bt0_enabled": {"type": "switch", "label": "不太灵影视"},
        "gy_enabled": {"type": "switch", "label": "观影"}
    },
    "私有资源站点设置": {
        "bt_login_username": {"type": "text", "label": "站点登录用户名"},
        "bt_login_password": {"type": "password", "label": "站点登录密码"},
        "bt_movie_base_url": {"type": "text", "label": "高清影视之家"},
        "bt_tv_base_url": {"type": "text", "label": "高清剧集网"}
    },
    "公开资源站点设置": {
        "bt0_login_username": {"type": "text", "label": "不太灵影视登录用户名"},
        "bt0_login_password": {"type": "password", "label": "不太灵影视登录密码"},
        "gy_login_username": {"type": "text", "label": "观影登录用户名"},
        "gy_login_password": {"type": "password", "label": "观影登录密码"},
        "btys_base_url": {"type": "text", "label": "BT影视"},
        "bt0_base_url": {"type": "text", "label": "不太灵影视"},
        "gy_base_url": {"type": "text", "label": "观影"}
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

    # 确保 "定时任务" 始终是最后一项
    if "定时任务" in grouped_config_data:
        timed_task = grouped_config_data.pop("定时任务")
        grouped_config_data["定时任务"] = timed_task

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

@app.route('/api/browse_directory', methods=['GET'])
@login_required
def browse_directory():
    """
    浏览目录结构的API接口
    """
    path = request.args.get('path', '/')
    try:
        # 安全检查，确保路径在允许的范围内
        if path == '/':
            # 允许访问根目录下的所有路径
            items = []
            try:
                for item in os.listdir(path):
                    item_path = os.path.join(path, item)
                    # 只显示目录
                    if os.path.isdir(item_path):
                        items.append({
                            'name': item,
                            'path': item_path,
                            'is_dir': True
                        })
            except PermissionError:
                return jsonify({'error': '没有权限访问根目录'}), 403
                
            # 按名称排序
            items.sort(key=lambda x: x['name'].lower())
            return jsonify({'path': path, 'items': items})
        
        # 确保路径存在且为目录
        if not os.path.exists(path) or not os.path.isdir(path):
            return jsonify({'error': '路径不存在或不是目录'}), 400
            
        items = []
        try:
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                items.append({
                    'name': item,
                    'path': item_path,
                    'is_dir': os.path.isdir(item_path)
                })
        except PermissionError:
            return jsonify({'error': '没有权限访问该目录'}), 403
            
        # 按目录和名称排序
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        # 添加上级目录
        parent_path = os.path.dirname(path)
        if parent_path != path:  # 不是根目录
            items.insert(0, {
                'name': '..',
                'path': parent_path,
                'is_dir': True
            })
            
        return jsonify({'path': path, 'items': items})
    except Exception as e:
        logger.error(f"浏览目录失败: {e}")
        return jsonify({'error': '浏览目录失败'}), 500

@app.route('/api/create_directory', methods=['POST'])
@login_required
def create_directory():
    """
    创建新目录的API接口
    """
    try:
        data = request.json
        parent_path = data.get('path')
        dir_name = data.get('dir_name')
        
        if not parent_path or not dir_name:
            return jsonify({'error': '缺少必要参数'}), 400
            
        # 防止目录遍历攻击
        if '..' in dir_name or dir_name.startswith('/'):
            return jsonify({'error': '无效的目录名称'}), 400
            
        new_dir_path = os.path.join(parent_path, dir_name)
        
        # 检查目录是否已存在
        if os.path.exists(new_dir_path):
            return jsonify({'error': '目录已存在'}), 400
            
        # 创建目录
        os.makedirs(new_dir_path, exist_ok=True)
        logger.info(f"成功创建目录: {new_dir_path}")
        
        # 返回新建目录的完整路径
        return jsonify({'message': '目录创建成功', 'path': new_dir_path}), 200
    except Exception as e:
        logger.error(f"创建目录失败: {e}")
        return jsonify({'error': '创建目录失败'}), 500

@app.route('/api/rename_directory', methods=['POST'])
@login_required
def rename_directory():
    """
    重命名目录的API接口
    """
    try:
        data = request.json
        old_path = data.get('old_path')
        new_name = data.get('new_name')
        
        if not old_path or not new_name:
            return jsonify({'error': '缺少必要参数'}), 400
            
        # 防止目录遍历攻击
        if '..' in new_name or new_name.startswith('/'):
            return jsonify({'error': '无效的目录名称'}), 400
            
        # 确保原路径存在且为目录
        if not os.path.exists(old_path) or not os.path.isdir(old_path):
            return jsonify({'error': '原目录不存在或不是目录'}), 400
            
        # 构造新路径
        parent_path = os.path.dirname(old_path)
        new_path = os.path.join(parent_path, new_name)
        
        # 检查新路径是否已存在
        if os.path.exists(new_path):
            return jsonify({'error': '目标目录已存在'}), 400
            
        # 重命名目录
        os.rename(old_path, new_path)
        logger.info(f"成功重命名目录: {old_path} -> {new_path}")
        
        return jsonify({'message': '目录重命名成功', 'path': new_path}), 200
    except Exception as e:
        logger.error(f"重命名目录失败: {e}")
        return jsonify({'error': '重命名目录失败'}), 500

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
    
    # 获取 download_type 配置
    download_type_config = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('download_type',)).fetchone()
    download_type = download_type_config['VALUE'] if download_type_config else None
    
    # 获取 delete_with_files 配置
    delete_with_files_config = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('delete_with_files',)).fetchone()
    if not delete_with_files_config:
        # 如果配置项不存在，创建默认配置
        db.execute('INSERT INTO CONFIG (OPTION, VALUE) VALUES (?, ?)', ('delete_with_files', 'False'))
        db.commit()
        delete_with_files = False
    else:
        delete_with_files = delete_with_files_config['VALUE'] == 'True'

    # 从会话中获取用户昵称和头像
    nickname = session.get('nickname')
    avatar_url = session.get('avatar_url')

    # 根据 download_type 使用不同模板
    if download_type == 'xunlei':
        template_name = 'xunlei.html'
    else:
        template_name = 'download_mgmt.html'

    # 将信息传递给模板
    return render_template(template_name, nickname=nickname, avatar_url=avatar_url, download_mgmt=download_mgmt_config, delete_with_files=delete_with_files, version=APP_VERSION)


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

# 批量操作（启动、暂停、删除）的API
@app.route('/api/download/<action>', methods=['POST'])
@login_required
def bulk_action(action):
    try:
        data = request.json
        client = get_downloader_client()

        # 获取任务 ID 列表
        task_ids = data.get("ids", [])
        logger.info(f"执行操作 {action}，任务ID列表: {task_ids}")
        
        if not task_ids:
            return jsonify({"error": "任务 ID 列表为空"}), 400

        # 获取 delete_with_files 配置（仅在删除操作时使用）
        delete_with_files = False
        if action == "delete":
            db = get_db()
            delete_with_files_config = db.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('delete_with_files',)).fetchone()
            delete_with_files = delete_with_files_config and delete_with_files_config['VALUE'] == 'True'
            logger.info(f"delete_with_files 配置: {delete_with_files}")

        # 执行批量操作
        if action == "start":
            if isinstance(client, TransmissionClient):
                client.start_torrent([int(task_id) for task_id in task_ids])
            else:
                # 对于qBittorrent，逐个处理任务以确保所有任务都被正确处理
                for task_id in task_ids:
                    try:
                        client.torrents_resume(hashes=task_id)
                        logger.info(f"已启动任务: {task_id}")
                    except Exception as e:
                        logger.error(f"启动任务 {task_id} 失败: {e}")
        elif action == "pause":
            if isinstance(client, TransmissionClient):
                client.stop_torrent([int(task_id) for task_id in task_ids])
            else:
                # 对于qBittorrent，逐个处理任务
                for task_id in task_ids:
                    try:
                        client.torrents_pause(hashes=task_id)
                        logger.info(f"已暂停任务: {task_id}")
                    except Exception as e:
                        logger.error(f"暂停任务 {task_id} 失败: {e}")
        elif action == "delete":
            if isinstance(client, TransmissionClient):
                client.remove_torrent([int(task_id) for task_id in task_ids], delete_data=delete_with_files)
            else:
                # 对于qBittorrent，逐个处理任务
                for task_id in task_ids:
                    try:
                        client.torrents_delete(delete_files=delete_with_files, hashes=task_id)
                        logger.info(f"已删除任务: {task_id}")
                    except Exception as e:
                        logger.error(f"删除任务 {task_id} 失败: {e}")
        else:
            return jsonify({"error": "无效的操作"}), 400

        logger.info(f"{action} 操作成功完成")
        return jsonify({"message": f"{action} 成功"})
    except Exception as e:
        logger.error(f"批量操作失败: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# 用于切换 delete_with_files 设置
@app.route('/api/download/toggle_delete_with_files', methods=['POST'])
@login_required
def toggle_delete_with_files():
    try:
        data = request.json
        enabled = data.get("enabled", False)
        
        db = get_db()
        # 更新配置
        db.execute('UPDATE CONFIG SET VALUE = ? WHERE OPTION = ?', 
                  ('True' if enabled else 'False', 'delete_with_files'))
        db.commit()
        
        logger.info(f"删除任务时同时删除本地文件设置已更新为: {enabled}")
        return jsonify({"message": "设置已更新"})
    except Exception as e:
        logger.error(f"更新设置失败: {e}")
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

@app.route('/test_downloader_connection', methods=['POST'])
@login_required
def test_downloader_connection():
    """
    测试下载器连接
    """
    try:
        data = request.json
        downloader = data.get('downloader')
        host = data.get('host')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        
        if downloader == 'transmission':
            client = TransmissionClient(
                host=host,
                port=port,
                username=username,
                password=password
            )
            # 尝试获取会话信息来测试连接
            client.get_session()
            return jsonify({"success": True, "message": "Transmission连接成功"})
            
        elif downloader == 'qbittorrent':
            client = QbittorrentClient(
                host=f"http://{host}:{port}",
                username=username,
                password=password
            )
            # 尝试获取应用版本来测试连接
            client.app_version()
            return jsonify({"success": True, "message": "qBittorrent连接成功"})
            
        else:
            return jsonify({"success": False, "message": "不支持的下载器类型"}), 400
            
    except Exception as e:
        logger.error(f"下载器连接测试失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 200

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
        "https://gh-proxy.net/",
        "https://gitproxy.click/",
        "https://github-proxy.lixxing.top/",
        "https://tvv.tw/"
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

@app.route('/health_check', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/restart_program', methods=['POST'])
@login_required
def restart_program():
    """
    重启程序：结束主进程以触发自动重启
    """
    try:
        logger.info("开始执行程序重启操作")
        
        # 检查是否有重启权限
        if not session.get('user_id'):
            logger.warning("未授权用户尝试执行重启")
            return jsonify({"error": "未授权的操作"}), 403

        logger.info("准备重启程序，正在结束主进程...")
        
        # 异步重启容器
        def restart_container():
            logger.info("正在重启容器...")
            time.sleep(2)
            # 查找并结束主进程
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
        
        # 启动后台线程执行重启操作
        threading.Thread(target=restart_container).start()
        
        return jsonify({"message": "重启命令已发送！程序将自动重启。"}), 200
        
    except Exception as e:
        logger.error(f"重启程序失败: {e}")
        return jsonify({"error": "重启失败，请稍后再试。"}), 500

@app.route('/reset_program', methods=['POST'])
@login_required
def reset_program():
    """
    重置程序：删除/config目录中的所有文件并重启容器，但保留client_id文件
    """
    try:
        logger.info("开始执行程序重置操作")
        
        # 检查是否有重置权限
        if not session.get('user_id'):
            logger.warning("未授权用户尝试执行重置")
            return jsonify({"error": "未授权的操作"}), 403

        # 删除/config目录中的所有文件和子目录，但保留/config目录本身和client_id文件
        config_dir = '/config'
        if os.path.exists(config_dir):
            for item in os.listdir(config_dir):
                # 跳过client_id文件
                if item == 'client_id':
                    logger.info("保留client_id文件")
                    continue
                    
                item_path = os.path.join(config_dir, item)
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                    logger.info(f"已删除文件: {item_path}")
                elif os.path.isdir(item_path):
                    import shutil
                    shutil.rmtree(item_path)
                    logger.info(f"已删除目录: {item_path}")
        
        logger.info("配置文件已清理完成，准备重启容器")
        
        # 异步重启容器
        def restart_container():
            logger.info("正在重启容器...")
            time.sleep(2)
            # 查找并结束主进程
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
        
        # 启动后台线程执行重启操作
        threading.Thread(target=restart_container).start()
        
        return jsonify({"message": "重置成功！程序将重启以恢复默认配置。"}), 200
        
    except Exception as e:
        logger.error(f"重置程序失败: {e}")
        return jsonify({"error": "重置失败，请稍后再试。"}), 500

@app.route('/check_update', methods=['GET'])
@login_required
def check_update():
    try:
        # 当前版本号
        current_version = APP_VERSION

        # GitHub API 地址和代理地址
        repo_url = "https://api.github.com/repos/smysong/mediamaster-v2/releases"
        latest_release_url = "https://api.github.com/repos/smysong/mediamaster-v2/releases/latest"
        proxy_url = "https://gh.llkk.cc/https://api.github.com/repos/smysong/mediamaster-v2/releases"
        proxy_latest_url = "https://gh.llkk.cc/https://api.github.com/repos/smysong/mediamaster-v2/releases/latest"

        # 获取所有发布版本
        try:
            response = requests.get(repo_url, timeout=5)
            if response.status_code != 200:
                raise Exception(f"主地址返回异常: {response.text}")
        except Exception as e:
            logger.warning(f"主地址连接失败，尝试代理: {e}")
            response = requests.get(proxy_url, timeout=8)

        if response.status_code != 200:
            logger.error(f"无法获取 GitHub 版本信息: {response.text}")
            return jsonify({"error": "无法连接到 GitHub，请稍后再试。"}), 500

        releases = response.json()
        
        # 获取GitHub标记的最新稳定版本
        latest_stable_release = None
        try:
            latest_response = requests.get(latest_release_url, timeout=5)
            if latest_response.status_code == 200:
                latest_stable_release = latest_response.json()
        except Exception as e:
            logger.warning(f"获取latest release失败，尝试代理: {e}")
            try:
                latest_response = requests.get(proxy_latest_url, timeout=8)
                if latest_response.status_code == 200:
                    latest_stable_release = latest_response.json()
            except Exception as e2:
                logger.warning(f"代理获取latest release也失败: {e2}")

        # 如果无法获取GitHub标记的latest release，则查找第一个非预发布版本
        if not latest_stable_release:
            for release in releases:
                if not release.get("prerelease"):
                    latest_stable_release = release
                    break
        
        # 获取最新的预发布版本
        latest_prerelease_release = None
        for release in releases:
            if release.get("prerelease"):
                latest_prerelease_release = release
                break

        # 构建返回数据
        result = {
            "current_version": current_version,
        }
        
        # 处理稳定版信息
        if latest_stable_release:
            stable_version = latest_stable_release.get("tag_name", "").lstrip("v")
            result["latest_stable_version"] = stable_version
            result["stable_release_notes"] = latest_stable_release.get("body", "无更新说明")
            result["stable_update_available"] = compare_versions(current_version, stable_version)
        else:
            result["latest_stable_version"] = None
            result["stable_release_notes"] = None
            result["stable_update_available"] = False
            
        # 处理预发布版信息
        if latest_prerelease_release:
            prerelease_version = latest_prerelease_release.get("tag_name", "").lstrip("v")
            result["latest_prerelease_version"] = prerelease_version
            result["prerelease_release_notes"] = latest_prerelease_release.get("body", "无更新说明")
            result["prerelease_update_available"] = compare_versions(current_version, prerelease_version)
        else:
            result["latest_prerelease_version"] = None
            result["prerelease_release_notes"] = None
            result["prerelease_update_available"] = False
            
        # 总体更新可用性（任一版本有更新即为可用）
        result["is_update_available"] = result["stable_update_available"] or result["prerelease_update_available"]

        return jsonify(result)
    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return jsonify({"error": "检查更新失败，请稍后再试。"}), 500

@app.route('/perform_update', methods=['POST'])
@login_required
def perform_update():
    try:
        # 获取当前版本号
        current_version = APP_VERSION
        
        # 获取更新类型参数（latest 或 prerelease）
        update_type = request.json.get('type', 'latest')  # 默认更新到最新稳定版

        # 检查是否有更新权限
        if not session.get('user_id'):
            logger.warning("未授权用户尝试执行更新")
            return jsonify({"error": "未授权的操作"}), 403

        logger.info(f"开始执行更新操作，更新类型: {update_type}")

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

        # 步骤2: 根据更新类型执行不同的更新操作
        if update_type == 'prerelease':
            # 更新到最新的预发布版本
            logger.info("正在获取最新的预发布版本标签...")
            
            # 先获取所有发布版本信息
            repo_url = "https://api.github.com/repos/smysong/mediamaster-v2/releases"
            proxy_url = "https://gh.llkk.cc/https://api.github.com/repos/smysong/mediamaster-v2/releases"
            
            try:
                response = requests.get(repo_url, timeout=5)
            except Exception as e:
                logger.warning(f"主地址连接失败，尝试代理: {e}")
                response = requests.get(proxy_url, timeout=8)
            
            if response.status_code != 200:
                error_message = f"无法获取 GitHub 版本信息: {response.text}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            releases = response.json()
            
            # 查找最新的预发布版本
            latest_prerelease_release = None
            for release in releases:
                if release.get("prerelease"):
                    latest_prerelease_release = release
                    break
            
            if not latest_prerelease_release:
                error_message = "未找到任何预发布版本"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            prerelease_version_tag = latest_prerelease_release.get("tag_name")
            logger.info(f"最新的预发布版本标签: {prerelease_version_tag}")
            
            # 拉取指定标签的代码
            logger.info("正在从 Git 仓库拉取最新预发布版本代码...")
            fetch_result = subprocess.run(
                ['git', 'fetch', '--all'],
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            if fetch_result.returncode != 0:
                error_message = f"Git fetch 失败: {fetch_result.stderr}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            # 检出预发布版本
            checkout_result = subprocess.run(
                ['git', 'checkout', prerelease_version_tag],
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            if checkout_result.returncode != 0:
                error_message = f"Git checkout 失败: {checkout_result.stderr}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            # 拉取最新代码
            pull_result = subprocess.run(
                ['git', 'pull', 'origin', prerelease_version_tag],
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            if pull_result.returncode != 0:
                error_message = f"Git pull 失败: {pull_result.stderr}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            logger.info(f"Git 拉取预发布版本成功: {pull_result.stdout}")
        else:
            # 更新到最新稳定版本（默认行为）
            logger.info("正在获取最新的稳定版本标签...")
            
            # 先获取所有发布版本信息
            repo_url = "https://api.github.com/repos/smysong/mediamaster-v2/releases"
            proxy_url = "https://gh.llkk.cc/https://api.github.com/repos/smysong/mediamaster-v2/releases"
            
            try:
                response = requests.get(repo_url, timeout=5)
            except Exception as e:
                logger.warning(f"主地址连接失败，尝试代理: {e}")
                response = requests.get(proxy_url, timeout=8)
            
            if response.status_code != 200:
                error_message = f"无法获取 GitHub 版本信息: {response.text}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            releases = response.json()
            
            # 查找最新的稳定版本
            latest_stable_release = None
            for release in releases:
                if not release.get("prerelease"):
                    latest_stable_release = release
                    break
            
            if not latest_stable_release:
                error_message = "未找到任何稳定版本"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            stable_version_tag = latest_stable_release.get("tag_name")
            logger.info(f"最新的稳定版本标签: {stable_version_tag}")
            
            # 拉取指定标签的代码
            logger.info("正在从 Git 仓库拉取最新稳定版本代码...")
            fetch_result = subprocess.run(
                ['git', 'fetch', '--all'],
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            if fetch_result.returncode != 0:
                error_message = f"Git fetch 失败: {fetch_result.stderr}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            # 检出稳定版本
            checkout_result = subprocess.run(
                ['git', 'checkout', stable_version_tag],
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            if checkout_result.returncode != 0:
                error_message = f"Git checkout 失败: {checkout_result.stderr}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            # 拉取最新代码
            pull_result = subprocess.run(
                ['git', 'pull', 'origin', stable_version_tag],
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            if pull_result.returncode != 0:
                error_message = f"Git pull 失败: {pull_result.stderr}"
                logger.error(error_message)
                return jsonify({"error": error_message}), 500
            
            logger.info(f"Git 拉取稳定版本成功: {pull_result.stdout}")

        # 步骤3: 安装依赖（如果有新的依赖）
        logger.info("正在安装新依赖...")
        install_result = subprocess.run(
            ['pip', 'install', '-r', 'requirements.txt', '--index-url', 'https://mirrors.aliyun.com/pypi/simple/'],
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

if __name__ == '__main__':
    logger.info("程序已启动")
    # 创建硬链接
    src_dir = '/config/avatars'
    dst_dir = '/app/static/uploads/avatars'
    create_soft_link(src_dir, dst_dir)
    
    # 支持通过环境变量设置端口，默认为8888
    port = 8888
    try:
        port_env = os.environ.get('PORT')
        if port_env:
            port = int(port_env)
            logger.info(f"使用自定义端口: {port}")
        else:
            logger.info("使用默认端口: 8888")
    except (ValueError, TypeError):
        logger.warning(f"环境变量PORT值无效，使用默认端口: 8888")
    
    app.run(host='0.0.0.0', port=port, debug=False)