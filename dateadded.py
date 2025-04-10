import os
import re
import sqlite3
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/dateadded.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def update_dateadded(directory):
    # 遍历指定目录及其子目录中的所有文件
    logging.info(f"开始遍历目录及其子目录: {directory}")
    for root, dirs, files in os.walk(directory):
        # 排除 music 目录（不区分大小写）
        dirs[:] = [d for d in dirs if d.lower() != 'music']
        
        for filename in files:
            # 排除 artist.nfo 文件（不区分大小写）
            if filename.lower().endswith('.nfo') and not filename.lower() == 'artist.nfo':
                file_path = os.path.join(root, filename)
                logging.info(f"处理文件: {file_path}")
                with open(file_path, 'r', encoding='utf-8') as file:
                    content = file.read()

                # 使用正则表达式查找<dateadded>, <releasedate>和<aired>标签
                dateadded_match = re.search(r'<dateadded>(.*?)</dateadded>', content, re.DOTALL)
                releasedate_match = re.search(r'<releasedate>(.*?)</releasedate>', content, re.DOTALL)
                aired_match = re.search(r'<aired>(.*?)</aired>', content, re.DOTALL)

                if dateadded_match:
                    dateadded_content = dateadded_match.group(1)
                    logging.info(f"添加日期: {dateadded_content}")

                    if releasedate_match:
                        replacement_content = releasedate_match.group(1)
                        logging.info(f"发行日期: {replacement_content}")
                    elif aired_match:
                        replacement_content = aired_match.group(1)
                        logging.info(f"播出日期: {replacement_content}")
                    else:
                        logging.warning(f"未找到 [发行日期] 或 [播出日期] 标签在文件: {file_path}")
                        continue

                    # 替换<dateadded>标签中的内容
                    updated_content = content.replace(f'<dateadded>{dateadded_content}</dateadded>',
                                                      f'<dateadded>{replacement_content}</dateadded>')
                    logging.info(f"更新 [添加日期] 为: {replacement_content}")

                    # 将更新后的内容写回文件
                    with open(file_path, 'w', encoding='utf-8') as file:
                        file.write(updated_content)

                    logging.info(f'更新完成: {file_path}')
                else:
                    logging.warning(f"未找到 [添加日期] 标签在文件: {file_path}")

def get_config_value(db_path, option):
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
    if dateadded_enabled.lower() == "true":  # 显式检查是否为 "true"
        update_dateadded(media_dir)
    else:
        logging.info('添加日期功能已禁用.')