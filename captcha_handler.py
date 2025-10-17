import logging
import time
import base64
import re
import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException

class CaptchaHandler:
    def __init__(self, driver, ocr_api_key):
        self.driver = driver
        self.ocr_api_key = ocr_api_key

    def handle_captcha(self, url):
        """
        统一处理所有类型的验证码
        """
        max_verification_attempts = 3
        verification_attempt = 0
        
        while verification_attempt < max_verification_attempts:
            verification_attempt += 1
            try:
                self.driver.get(url)
                
                # 检查页面是否已经成功加载
                if self._is_page_loaded():
                    logging.info("页面已成功加载，无需验证码")
                    return
                
                # 检测验证码类型并处理
                captcha_type = self._detect_captcha_type()
                
                if captcha_type == "slide":
                    self._handle_slide_captcha()
                elif captcha_type == "image":
                    self._handle_image_captcha()
                elif captcha_type == "checkbox":
                    self._handle_checkbox_captcha()
                elif captcha_type == "cloudflare_text":
                    self._handle_cloudflare_captcha("text")
                elif captcha_type == "cloudflare_checkbox":
                    self._handle_cloudflare_captcha("checkbox")
                else:
                    logging.info("未检测到验证码")
                    if self._is_page_loaded():
                        return
                    else:
                        raise Exception("页面加载失败且未检测到验证码")
                
                # 验证页面是否成功跳转
                if self._wait_for_page_redirect(url):
                    if self._is_page_loaded():
                        logging.info("验证码验证成功")
                        return
                    else:
                        logging.warning("页面跳转完成但内容未正确加载")
                        if verification_attempt < max_verification_attempts:
                            continue
                        else:
                            raise Exception("验证失败，已达到最大验证尝试次数")
                else:
                    logging.warning("页面未成功跳转")
                    if verification_attempt < max_verification_attempts:
                        continue
                    else:
                        raise Exception("验证失败，已达到最大验证尝试次数")
                        
            except Exception as e:
                logging.error(f"验证码处理出错: {e}")
                if verification_attempt < max_verification_attempts:
                    logging.info(f"第 {verification_attempt} 次验证失败，准备进行第 {verification_attempt + 1} 次验证尝试")
                    continue
                else:
                    logging.error("已达到最大验证尝试次数，验证失败")
                    raise

    def _detect_captcha_type(self):
        """
        检测验证码类型
        """
        try:
            # 检查滑动或图片验证码
            captcha_prompt = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "p.ui-prompt"))
            )
            if captcha_prompt.text in ["滑动上面方块到右侧解锁", "Slide to Unlock"]:
                return "slide"
            elif captcha_prompt.text in ["请输入上面的验证码", "Please enter the verification code above"]:
                return "image"
        except TimeoutException:
            pass
        
        try:
            # 检查复选框验证码
            captcha_prompt = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.container .title"))
            )
            if "请确认您不是机器人" in captcha_prompt.text:
                return "checkbox"
        except TimeoutException:
            pass
        
        try:
            # 检查Cloudflare验证（文本验证类型）
            cf_text = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "Truv1"))
            )
            if "正在验证您是否是真人" in cf_text.text or "verifying you are human" in cf_text.text.lower():
                return "cloudflare_text"
        except TimeoutException:
            pass
        
        try:
            # 检查Cloudflare验证（复选框验证类型）
            cb_label = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "label.cb-lb"))
            )
            if "确认您是真人" in cb_label.text:
                return "cloudflare_checkbox"
        except TimeoutException:
            pass
            
        # 检查是否是Cloudflare验证页面但未被上面的条件捕获
        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            if "正在验证您是否是真人" in page_text or "cloudflare" in page_text.lower():
                # 尝试确定具体类型
                try:
                    self.driver.find_element(By.ID, "Truv1")
                    return "cloudflare_text"
                except:
                    try:
                        self.driver.find_element(By.CSS_SELECTOR, "label.cb-lb")
                        return "cloudflare_checkbox"
                    except:
                        # 默认当作文本验证处理
                        return "cloudflare_text"
        except:
            pass
            
        return "none"

    def _handle_slide_captcha(self):
        """
        处理滑动验证码
        """
        logging.info("检测到滑动验证码，开始验证")

        # 等待滑块元素出现
        handler = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "handler"))
        )

        # 等待目标位置元素出现
        target = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "handler-placeholder"))
        )

        # 获取滑块的初始位置
        handler_location = handler.location
        target_location = target.location

        # 计算滑块需要移动的距离
        move_distance = target_location['x'] - handler_location['x']

        # 使用 ActionChains 模拟拖动滑块
        actions = ActionChains(self.driver)
        actions.click_and_hold(handler).move_by_offset(move_distance, 0).release().perform()

        logging.info("滑块已成功拖动到目标位置")

    def _handle_image_captcha(self):
        """
        处理图片验证码
        """
        logging.info("检测到图片验证码，开始识别")
        
        # 检查是否配置了 OCR API Key
        if not self.ocr_api_key:
            logging.warning("未配置 OCR API Key，无法自动识别图片验证码")
            raise Exception("未配置 OCR API Key，无法处理图片验证码")
        
        max_retries = 10
        retry_count = 0
        captcha_code = None
        
        while retry_count < max_retries and not captcha_code:
            retry_count += 1
            logging.info(f"验证码识别尝试 {retry_count}/{max_retries}")
            
            # 等待验证码图片加载
            captcha_img = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "ui-captcha-image"))
            )
            
            try:
                # 使用Selenium截图方式直接获取验证码图片数据
                screenshot_data = captcha_img.screenshot_as_png
                logging.info("通过selenium截图方式获取验证码图片数据")
                
                # 将截图数据转换为base64编码
                image_base64 = base64.b64encode(screenshot_data).decode('utf-8')
                
                # 使用OCR.space API进行验证码识别
                captcha_code = self._ocr_recognize(image_base64)
                
                if captcha_code:
                    logging.info(f"识别出的验证码: {captcha_code}")
                    break
                    
            except Exception as e:
                logging.error(f"验证码识别失败: {e}")
            
            # 如果没有成功识别，刷新页面获取新的验证码
            if retry_count < max_retries and not captcha_code:
                logging.info("刷新页面以获取新的验证码")
                self.driver.refresh()
                time.sleep(2)
        
        if not captcha_code:
            raise Exception("超过最大重试次数，未能识别出有效的6位数字验证码")
        
        # 输入验证码并提交
        self._submit_captcha(captcha_code)

    def _ocr_recognize(self, image_base64):
        """
        使用OCR识别验证码
        """
        try:
            payload = {
                'base64Image': f'data:image/png;base64,{image_base64}',
                'apikey': self.ocr_api_key,
                'language': 'eng',
                'OCREngine': 2
            }
            
            ocr_response = requests.post('https://api.ocr.space/parse/image', data=payload)
            
            if ocr_response.status_code == 200:
                result = ocr_response.json()
                if result['IsErroredOnProcessing']:
                    logging.warning(f"OCR处理错误: {result['ErrorMessage']}")
                    return None
                else:
                    parsed_results = result['ParsedResults']
                    if parsed_results:
                        captcha_text = parsed_results[0]['ParsedText']
                        # 使用正则表达式提取6位数字验证码
                        match = re.search(r'\d{6}', captcha_text)
                        if match:
                            return match.group()
                        else:
                            logging.warning(f"识别的文本不是6位数字: {captcha_text}")
                            return None
                    else:
                        logging.warning("OCR没有返回解析结果")
                        return None
            else:
                logging.warning(f"OCR API请求失败，状态码: {ocr_response.status_code}")
                return None
                
        except Exception as e:
            logging.error(f"OCR识别异常: {e}")
            return None

    def _submit_captcha(self, captcha_code):
        """
        提交验证码
        """
        captcha_input = self.driver.find_element(By.ID, "GOEDGE_WAF_CAPTCHA_CODE")
        captcha_input.send_keys(captcha_code)
        
        # 提交表单
        submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        submit_button.click()
        
        logging.info("验证码已提交")

    def _handle_checkbox_captcha(self):
        """
        处理复选框验证码
        """
        logging.info("检测到验证码，开始验证")

        # 等待复选框元素出现
        checkbox = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "checkbox"))
        )

        # 使用 ActionChains 模拟点击复选框
        actions = ActionChains(self.driver)
        actions.move_to_element(checkbox).click().perform()

        logging.info("复选框已成功点击")

        # 等待加载指示器消失，表示验证完成
        WebDriverWait(self.driver, 30).until_not(
            EC.presence_of_element_located((By.ID, "loading-indicator"))
        )

        logging.info("验证码验证成功")

    def _handle_cloudflare_captcha(self, captcha_subtype="text"):
        """
        处理Cloudflare验证
        等待系统自动完成验证过程
        """
        if captcha_subtype == "checkbox":
            logging.info("检测到Cloudflare复选框验证，开始处理")
            try:
                # 等待复选框元素出现并点击
                checkbox = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "label.cb-lb input[type='checkbox']"))
                )
                actions = ActionChains(self.driver)
                actions.move_to_element(checkbox).click().perform()
                logging.info("Cloudflare复选框已点击")
                
                # 等待验证成功，通过检查页面关键词判断
                WebDriverWait(self.driver, 60).until(
                    lambda driver: self._is_page_loaded() or 
                                (driver.find_elements(By.ID, "success-text") and 
                                "成功" in driver.find_element(By.ID, "success-text").text)
                )
                logging.info("Cloudflare复选框验证成功完成")
                time.sleep(5)
            except TimeoutException:
                logging.warning("Cloudflare复选框验证可能未完成或超时")
        else:  # 默认处理文本验证类型
            logging.info("检测到Cloudflare文本验证，等待系统自动验证")
            
            try:
                # 等待验证成功，通过检查页面关键词判断
                WebDriverWait(self.driver, 60).until(
                    lambda driver: self._is_page_loaded() or
                                (driver.find_elements(By.ID, "Truv1") and
                                "验证成功" in driver.find_element(By.ID, "Truv1").text)
                )
                logging.info("Cloudflare文本验证成功完成")
                time.sleep(5)
            except TimeoutException:
                # 检查是否仍在验证中
                try:
                    # 获取页面所有文本内容进行检查
                    page_text = self.driver.find_element(By.TAG_NAME, "body").text
                    if "正在等待" in page_text or "正在验证" in page_text or "是真人" in page_text:
                        logging.info("Cloudflare仍在验证中，继续等待")
                        # 再等待一段时间
                        time.sleep(30)
                        # 再次检查是否验证成功
                        try:
                            WebDriverWait(self.driver, 30).until(
                                lambda driver: self._is_page_loaded()
                            )
                            logging.info("Cloudflare验证成功完成")
                            time.sleep(5)
                            return
                        except TimeoutException:
                            pass
                except:
                    pass
                logging.warning("Cloudflare验证可能未完成或超时")

    def _is_page_loaded(self):
        """
        检查页面是否加载成功
        """
        try:
            page_source = self.driver.page_source
            
            # 检查页面是否包含"影视"、"剧集"或"观影"关键词中的任意一个
            return ("影视" in page_source or 
                    "剧集" in page_source or 
                    "观影" in page_source)
        except:
            return False

    def _wait_for_page_redirect(self, original_url, timeout=30):
        """
        等待页面跳转完成
        """
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.url_changes(original_url)
            )
            return True
        except TimeoutException:
            return False

    def close_popup_if_exists(self):
        """
        关闭可能存在的提示框
        """
        try:
            popup_close_button = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.popup-footer button"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(popup_close_button).click().perform()
            logging.info("成功点击'不再提醒'按钮")
        except TimeoutException:
            logging.info("未检测到提示框，无需操作")