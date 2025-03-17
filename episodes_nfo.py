import os
from xml.etree import ElementTree as ET
import logging
import sqlite3

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/episodes_nfo.log"),
        logging.StreamHandler()
    ]
)

global_config = {}

def load_config():
    try:
        conn = sqlite3.connect('/config/data.db')
        cursor = conn.cursor()
        cursor.execute("SELECT OPTION, VALUE FROM CONFIG")
        rows = cursor.fetchall()
        config_dict = {option: value for option, value in rows}
        global_config.update({
            "episodes_path": config_dict.get("episodes_path"),
            "nfo_exclude_dirs": config_dict.get("nfo_exclude_dirs").split(',')
        })
        logging.info("从数据库加载配置文件成功")
    except sqlite3.Error as e:
        logging.error(f"从数据库加载配置失败: {e}")
        exit(1)
    finally:
        if 'conn' in locals():
            conn.close()
def parse_nfo(file_path):
    """解析NFO文件，返回演员字典，键为tmdbid或imdbid，值为(name, role)元组"""
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        actors = {}
        for actor in root.findall('actor'):
            tmdbid_elem = actor.find('tmdbid')
            imdbid_elem = actor.find('imdbid')
            name_elem = actor.find('name')
            role_elem = actor.find('role')
            type_elem = actor.find('type')
            
            if name_elem is None:
                logging.warning(f"文件 {file_path} 中的演员缺少 name 标签")
                continue
            
            name = name_elem.text
            role = role_elem.text if role_elem is not None else "演员"
            
            if role_elem is None:
                # 创建新的 <role> 标签并插入到 <name> 和 <type> 之间
                role_elem = ET.SubElement(actor, 'role')
                role_elem.text = "演员"
                name_elem.tail = '\n  '  # 确保 <role> 标签在 <name> 标签之后换行插入
                role_elem.tail = '\n  '  # 确保 <role> 标签之后换行插入
                
                # 如果有 <type> 标签，确保 <role> 标签在 <type> 标签之前
                if type_elem is not None:
                    actor.remove(type_elem)
                    actor.append(type_elem)
                
                logging.info(f"为文件 {file_path} 中的演员添加了默认的 <role> 标签")
            
            tmdbid = tmdbid_elem.text if tmdbid_elem is not None else None
            imdbid = imdbid_elem.text if imdbid_elem is not None else None
            
            if tmdbid:
                actors[tmdbid] = (name, role)
            elif imdbid:
                actors[imdbid] = (name, role)
            else:
                logging.warning(f"文件 {file_path} 中的演员缺少 tmdbid 和 imdbid")
        
        # 保存更新后的 tvshow.nfo 文件
        if any(role_elem is None for role_elem in [actor.find('role') for actor in root.findall('actor')]):
            tree.write(file_path, encoding='utf-8', xml_declaration=True)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content.replace('><', '>\n<'))
        
        logging.info(f"解析了 {len(actors)} 位演员的信息，来源文件：{file_path}")
        return actors
    except Exception as e:
        logging.error(f"解析文件 {file_path} 时出错：{e}")
        return {}

def update_nfo(file_path, actors):
    """更新NFO文件中的演员角色信息"""
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        updated = False
        for actor in root.findall('actor'):
            tmdbid_elem = actor.find('tmdbid')
            imdbid_elem = actor.find('imdbid')
            name_elem = actor.find('name')
            role_elem = actor.find('role')
            type_elem = actor.find('type')
            
            if name_elem is None:
                logging.warning(f"文件 {file_path} 中的演员缺少 name 标签")
                continue
            
            tmdbid = tmdbid_elem.text if tmdbid_elem is not None else None
            imdbid = imdbid_elem.text if imdbid_elem is not None else None
            
            if tmdbid and tmdbid in actors:
                name_elem.text = actors[tmdbid][0]
                if role_elem is None:
                    # 创建新的 <role> 标签并插入到 <name> 和 <type> 之间
                    role_elem = ET.SubElement(actor, 'role')
                    role_elem.text = actors[tmdbid][1]
                    name_elem.tail = '\n  '  # 确保 <role> 标签在 <name> 标签之后换行插入
                    role_elem.tail = '\n  '  # 确保 <role> 标签之后换行插入
                    
                    # 如果有 <type> 标签，确保 <role> 标签在 <type> 标签之前
                    if type_elem is not None:
                        actor.remove(type_elem)
                        actor.append(type_elem)
                    
                    logging.info(f"为文件 {file_path} 中的演员添加了默认的 <role> 标签")
                else:
                    role_elem.text = actors[tmdbid][1]
                updated = True
            elif imdbid and imdbid in actors:
                name_elem.text = actors[imdbid][0]
                if role_elem is None:
                    # 创建新的 <role> 标签并插入到 <name> 和 <type> 之间
                    role_elem = ET.SubElement(actor, 'role')
                    role_elem.text = actors[imdbid][1]
                    name_elem.tail = '\n  '  # 确保 <role> 标签在 <name> 标签之后换行插入
                    role_elem.tail = '\n  '  # 确保 <role> 标签之后换行插入
                    
                    # 如果有 <type> 标签，确保 <role> 标签在 <type> 标签之前
                    if type_elem is not None:
                        actor.remove(type_elem)
                        actor.append(type_elem)
                    
                    logging.info(f"为文件 {file_path} 中的演员添加了默认的 <role> 标签")
                else:
                    role_elem.text = actors[imdbid][1]
                updated = True
        
        if updated:
            tree.write(file_path, encoding='utf-8', xml_declaration=True)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content.replace('><', '>\n<'))
            logging.info(f"已更新文件中的角色信息：{file_path}")
        else:
            logging.debug(f"文件无需更新：{file_path}")
    except Exception as e:
        logging.error(f"更新文件 {file_path} 时出错：{e}")

def process_directory(base_dir, exclude_dirs):
    """处理给定目录及其子目录中的NFO文件"""
    if any(exclude_dir in base_dir for exclude_dir in exclude_dirs):
        logging.debug(f"跳过排除目录：{base_dir}")
        return
    
    tvshow_nfo_path = os.path.join(base_dir, 'tvshow.nfo')
    if os.path.exists(tvshow_nfo_path):
        main_actors = parse_nfo(tvshow_nfo_path)
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                if file.endswith('.nfo') and 'Season' in root:  # 只处理季目录中的NFO文件
                    nfo_file_path = os.path.join(root, file)
                    update_nfo(nfo_file_path, main_actors)
    else:
        logging.warning(f"未找到文件 tvshow.nfo 在目录：{base_dir}")

def process_media_directory(media_dir, exclude_dirs):
    """递归处理媒体目录及其所有子目录"""
    for root, dirs, files in os.walk(media_dir):
        for dir_name in dirs:
            show_path = os.path.join(root, dir_name)
            if os.path.isdir(show_path):
                process_directory(show_path, exclude_dirs)

if __name__ == '__main__': 
    media_dir = global_config.get("episodes_path")
    exclude_dirs = global_config.get("nfo_exclude_dirs").split(',')
    
    process_media_directory(media_dir, exclude_dirs)