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
    # 遍历指定目录中的所有文件
    for filename in os.listdir(directory):
        if filename.endswith('.nfo'):
            file_path = os.path.join(directory, filename)
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()

            # 使用正则表达式查找<dateadded>和<releasedate>标签
            dateadded_match = re.search(r'<dateadded>(.*?)</dateadded>', content, re.DOTALL)
            releasedate_match = re.search(r'<releasedate>(.*?)</releasedate>', content, re.DOTALL)

            if dateadded_match and releasedate_match:
                dateadded_content = dateadded_match.group(1)
                releasedate_content = releasedate_match.group(1)

                # 替换<dateadded>标签中的内容
                updated_content = content.replace(f'<dateadded>{dateadded_content}</dateadded>',
                                                  f'<dateadded>{releasedate_content}</dateadded>')

                # 将更新后的内容写回文件
                with open(file_path, 'w', encoding='utf-8') as file:
                    file.write(updated_content)

                print(f'Updated {file_path}')

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
    if dateadded_enabled.lower() == "true":  # 显式检查是否为 "true"
        update_dateadded(media_dir)
    else:
        logging.info('添加日期功能已禁用.')