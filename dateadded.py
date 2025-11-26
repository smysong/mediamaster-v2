import os
import re
import sqlite3
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/dateadded.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def read_file_with_encoding(file_path):
    """
    尝试使用多种编码读取文件内容，失败则返回 None。
    """
    encodings = ['utf-8', 'gbk', 'iso-8859-1', 'latin-1']
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as file:
                return file.read()
        except UnicodeDecodeError:
            continue
    logging.error(f"无法解码文件: {file_path}，所有尝试的编码均失败")
    return None

def get_parent_nfo_date(file_path):
    """
    从 tvshow.nfo 或 season.nfo 获取日期信息
    """
    directory = os.path.dirname(file_path)
    season_nfo_path = os.path.join(directory, 'season.nfo')
    tvshow_nfo_path = None
    
    # 查找 tvshow.nfo 路径
    parent_dir = os.path.dirname(directory)
    if parent_dir:
        tvshow_nfo_path = os.path.join(parent_dir, 'tvshow.nfo')
    
    # 首先尝试从 season.nfo 获取日期
    if os.path.exists(season_nfo_path):
        content = read_file_with_encoding(season_nfo_path)
        if content:
            releasedate_match = re.search(r'<releasedate>(.*?)</releasedate>', content, re.DOTALL)
            aired_match = re.search(r'<aired>(.*?)</aired>', content, re.DOTALL)
            
            if releasedate_match:
                date_content = releasedate_match.group(1).strip()
                if not date_content.startswith('0001'):
                    return date_content
            elif aired_match:
                date_content = aired_match.group(1).strip()
                if not date_content.startswith('0001'):
                    return date_content
    
    # 如果 season.nfo 没有有效日期，则尝试 tvshow.nfo
    if tvshow_nfo_path and os.path.exists(tvshow_nfo_path):
        content = read_file_with_encoding(tvshow_nfo_path)
        if content:
            releasedate_match = re.search(r'<releasedate>(.*?)</releasedate>', content, re.DOTALL)
            aired_match = re.search(r'<aired>(.*?)</aired>', content, re.DOTALL)
            
            if releasedate_match:
                date_content = releasedate_match.group(1).strip()
                if not date_content.startswith('0001'):
                    return date_content
            elif aired_match:
                date_content = aired_match.group(1).strip()
                if not date_content.startswith('0001'):
                    return date_content
    
    return None

def update_dateadded(directory):
    """
    更新指定目录下所有 .nfo 文件中的 <dateadded> 标签值。
    """
    logging.debug(f"开始遍历目录及其子目录: {directory}")
    for root, dirs, files in os.walk(directory):
        # 排除 music 目录（不区分大小写）
        dirs[:] = [d for d in dirs if d.lower() != 'music']

        for filename in files:
            # 排除 artist.nfo 文件（不区分大小写）
            if filename.lower().endswith('.nfo') and not filename.lower() == 'artist.nfo':
                file_path = os.path.join(root, filename)
                logging.debug(f"处理文件: {file_path}")

                content = read_file_with_encoding(file_path)
                if content is None:
                    continue

                # 使用正则表达式查找<dateadded>, <releasedate>和<aired>标签
                dateadded_match = re.search(r'<dateadded>(.*?)</dateadded>', content, re.DOTALL)
                releasedate_match = re.search(r'<releasedate>(.*?)</releasedate>', content, re.DOTALL)
                aired_match = re.search(r'<aired>(.*?)</aired>', content, re.DOTALL)

                if dateadded_match:
                    dateadded_content = dateadded_match.group(1)
                    logging.debug(f"添加日期: {dateadded_content}")

                    # 提取 dateadded 的年月日部分
                    dateadded_date = dateadded_content.split()[0]

                    replacement_content = None
                    if releasedate_match:
                        replacement_content = releasedate_match.group(1).strip()
                        logging.debug(f"发行日期: {replacement_content}")
                    elif aired_match:
                        replacement_content = aired_match.group(1).strip()
                        logging.debug(f"播出日期: {replacement_content}")

                    # 检查是否有有效的本地日期
                    local_date_valid = replacement_content and not replacement_content.startswith('0001')
                    
                    # 如果本地日期无效，尝试从父级 nfo 文件获取日期
                    if not local_date_valid:
                        parent_date = get_parent_nfo_date(file_path)
                        if parent_date:
                            replacement_content = parent_date
                            logging.debug(f"从父级 NFO 获取日期: {replacement_content}")
                        else:
                            logging.warning(f"未找到有效的 [发行日期] 或 [播出日期] 标签在文件或其父级文件中: {file_path}")
                            continue
                    elif not replacement_content:
                        # 本地没有日期字段且无法从父级获取
                        logging.warning(f"未找到 [发行日期] 或 [播出日期] 标签在文件: {file_path}")
                        continue

                    # 检查年份是否为 0001（仅当我们没有从父级获取日期时）
                    if replacement_content.startswith('0001'):
                        logging.warning(f"[发行日期] 或 [播出日期] 年份无效 (0001)，跳过处理: {file_path}")
                        continue

                    # 比较 dateadded 的年月日部分与 replacement_content 是否相同
                    if dateadded_date == replacement_content:
                        logging.debug(f"[添加日期] 与 [发行日期] 或 [播出日期] 相同，跳过处理: {file_path}")
                        continue

                    # 替换<dateadded>标签中的内容
                    updated_content = content.replace(
                        f'<dateadded>{dateadded_content}</dateadded>',
                        f'<dateadded>{replacement_content}</dateadded>'
                    )
                    logging.info(f"更新 [添加日期] 为: {replacement_content}")

                    # 将更新后的内容写回文件
                    with open(file_path, 'w', encoding='utf-8') as file:
                        file.write(updated_content)

                    logging.info(f'更新完成: {file_path}')
                else:
                    logging.warning(f"未找到 [添加日期] 标签在文件: {file_path}")

def get_config_value(db_path, option):
    """
    从 SQLite 数据库中读取配置项。
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT VALUE FROM CONFIG WHERE OPTION = ?", (option,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

if __name__ == '__main__':
    db_path = '/config/data.db'
    media_dir = get_config_value(db_path, 'media_dir')
    dateadded_enabled = get_config_value(db_path, 'dateadded')
    logging.debug(f"从数据库获取配置: media_dir={media_dir}, dateadded={dateadded_enabled}")
    if dateadded_enabled and dateadded_enabled.lower() == "true":  # 显式检查是否为 "true"
        update_dateadded(media_dir)
    else:
        logging.info('添加日期功能已禁用.')