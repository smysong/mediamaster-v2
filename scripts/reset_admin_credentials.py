import sqlite3
import bcrypt
import os
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

# 数据库文件路径（与 database_manager.py 中一致）
DB_PATH = "/config/data.db"

def hash_password(password):
    """使用 bcrypt 对密码进行哈希"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode(), salt)
    return hashed.decode()

def reset_admin_credentials():
    """重置管理员账户凭据为默认值"""
    # 检查数据库文件是否存在
    if not os.path.exists(DB_PATH):
        logging.error(f"数据库文件 {DB_PATH} 不存在")
        return False
    
    try:
        # 连接数据库
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 默认用户名和密码
        default_username = "admin"
        default_password = "password"
        default_nickname = "管理员"
        default_avatar_url = "/static/img/avatar.png"
        
        # 哈希默认密码
        hashed_password = hash_password(default_password)
        
        # 更新或插入管理员用户记录
        cursor.execute("""
            UPDATE USERS 
            SET USERNAME = ?, NICKNAME = ?, AVATAR_URL = ?, PASSWORD = ?
            WHERE USERNAME = ?
        """, (default_username, default_nickname, default_avatar_url, hashed_password, default_username))
        
        # 如果没有更新任何记录，说明管理员用户不存在，需要插入
        if cursor.rowcount == 0:
            cursor.execute("""
                INSERT INTO USERS (USERNAME, NICKNAME, AVATAR_URL, PASSWORD)
                VALUES (?, ?, ?, ?)
            """, (default_username, default_nickname, default_avatar_url, hashed_password))
            logging.info("已创建新的管理员用户")
        else:
            logging.info("已更新现有管理员用户")
        
        # 提交更改
        conn.commit()
        conn.close()
        
        logging.info(f"管理员凭据已重置为默认值")
        logging.info(f"用户名: {default_username}")
        logging.info(f"密码: {default_password}")
        logging.info("请立即登录并修改密码!")
        
        return True
        
    except Exception as e:
        logging.error(f"重置管理员凭据时出错: {str(e)}")
        return False

if __name__ == "__main__":
    print("警告: 此操作将重置管理员账户凭据为默认值!")
    confirm = input("确认继续? (输入 'yes' 继续): ")
    
    if confirm.lower() == 'yes':
        success = reset_admin_credentials()
        if success:
            print("\n管理员凭据重置成功!")
        else:
            print("\n管理员凭据重置失败!")
    else:
        print("操作已取消")