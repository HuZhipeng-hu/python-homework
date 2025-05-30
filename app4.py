import requests
import json
from flask import Flask, request, jsonify, render_template, redirect, url_for
import pyodbc
from datetime import datetime
from sqlalchemy import create_engine, text
import random
import uuid
import pandas as pd

app = Flask(__name__)

# SQL Server 数据库配置
DB_SERVER = '(local)'
DB_NAME = 'student'
DB_USER = 'sa'
DB_PASSWORD = 'H1314.nice'

# 千问 API 相关配置
QIANWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QIANWEN_API_KEY = "sk-bb0e8f7910cd4bbe92c5a90ee98c3911"

def get_db():
    conn_str = f'DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_NAME};UID={DB_USER};PWD={DB_PASSWORD}'
    return pyodbc.connect(conn_str)


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 初始化受访者、访问、回答、问题表
    cursor.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='respondents' AND xtype='U')
        CREATE TABLE respondents (
            id INT IDENTITY(1,1) PRIMARY KEY,
            name NVARCHAR(255),
            age INT,
            gender NVARCHAR(10),
            education NVARCHAR(255),
            marital NVARCHAR(255),
            job NVARCHAR(255),
            residence NVARCHAR(255),
            chronic NVARCHAR(255),
            family_history NVARCHAR(255),
            life_event NVARCHAR(255),
            sleep NVARCHAR(255),
            exercise NVARCHAR(255),
            smoke_drink NVARCHAR(255),
            mood NVARCHAR(255),
            first_visit_date DATETIME
        )
    """)
    cursor.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='visits' AND xtype='U')
        CREATE TABLE visits (
            id INT IDENTITY(1,1) PRIMARY KEY,
            respondent_id INT,
            visit_number INT,
            visit_date DATETIME,
            FOREIGN KEY (respondent_id) REFERENCES respondents (id)
        )
    """)
    
    # 先删除已存在的responses表（如果存在）
    cursor.execute("""
        IF EXISTS (SELECT * FROM sysobjects WHERE name='responses' AND xtype='U')
        DROP TABLE responses
    """)
    
    # 重新创建responses表，使用FLOAT类型存储分数
    cursor.execute("""
        CREATE TABLE responses (
            chat_id INT,
            q_id INT,
            question NVARCHAR(MAX),
            answer NVARCHAR(MAX),
            score FLOAT,
            response_time DATETIME,
            FOREIGN KEY (chat_id) REFERENCES visits (id)
        )
    """)
    
    cursor.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='questions' AND xtype='U')
        CREATE TABLE questions (
            id INT IDENTITY(1,1) PRIMARY KEY,
            question_text NVARCHAR(MAX)
        )
    """)

    # 插入示例问题 (如已有可注释或移除)
    """sample_questions = [
        # ... 保持原有 20 条或更多 ...
    ]
    cursor.execute('DELETE FROM questions')
    cursor.executemany('INSERT INTO questions (question_text) VALUES (?)', [(q,) for q in sample_questions])"""

    conn.commit()
    conn.close()


def evaluate_qa(question, answer_text, respondent_info):
    """
    请求千问 API 对问答进行评分，考虑多个维度的专业评估
    """
    # 构建更专业的系统提示词
    system_prompt = """你是一位专业的心理咨询师，需要对来访者的回答进行多维度评估。
请从以下几个维度进行评分（每个维度0-100分）：
1. 情绪表达：评估来访者表达情绪的清晰度和深度
2. 认知水平：评估来访者对问题的理解和思考深度
3. 应对方式：评估来访者处理问题的积极性和有效性
4. 社会支持：评估来访者提及的社会支持系统情况
5. 风险程度：评估回答中体现的心理健康风险程度

请严格按照以下格式输出评分（不要添加其他文字）：
情绪表达:XX分
认知水平:XX分
应对方式:XX分
社会支持:XX分
风险程度:XX分
总分:XX分

注意：
- 分数必须是0-100之间的整数
- 不要添加任何其他解释或文字
- 严格按照上述格式输出"""

    # 构建包含受访者信息的提示词
    user_prompt = f"""来访者基本信息：
- 年龄：{respondent_info.get('age', '未知')}
- 性别：{respondent_info.get('gender', '未知')}
- 教育程度：{respondent_info.get('education', '未知')}
- 婚姻状况：{respondent_info.get('marital', '未知')}
- 慢性病史：{respondent_info.get('chronic', '未知')}
- 家族史：{respondent_info.get('family_history', '未知')}
- 生活事件：{respondent_info.get('life_event', '未知')}
- 睡眠状况：{respondent_info.get('sleep', '未知')}
- 运动情况：{respondent_info.get('exercise', '未知')}
- 烟酒情况：{respondent_info.get('smoke_drink', '未知')}
- 当前情绪：{respondent_info.get('mood', '未知')}

问题：{question}
回答：{answer_text}

请根据以上信息，对来访者的回答进行专业评估。"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {QIANWEN_API_KEY}"
    }
    
    data = {
        "model": "qwen-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }
    
    try:
        response = requests.post(QIANWEN_API_URL, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        api_response = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        
        # 解析API返回的评分结果
        try:
            # 提取总分
            total_score = None
            for line in api_response.split("\n"):
                if "总分:" in line:
                    try:
                        total_score = float(line.split("总分:")[-1].split("分")[0].strip())
                        break
                    except (ValueError, IndexError):
                        continue
            
            if total_score is None:
                print(f"无法从API响应中提取总分: {api_response}")
                return 50.0, "评分解析失败，使用默认分数"
            
            # 提取各维度分数
            scores = {}
            for line in api_response.split("\n"):
                if ":" in line and "分" in line:
                    try:
                        dimension, score = line.split(":")
                        dimension = dimension.strip()
                        score = float(score.split("分")[0].strip())
                        scores[dimension] = score
                    except (ValueError, IndexError):
                        continue
            
            # 根据受访者信息调整权重
            weight = 1.0
            if respondent_info.get('chronic') == '有':
                weight += 0.05
            if respondent_info.get('family_history') == '有':
                weight += 0.05
            if respondent_info.get('life_event') == '有重大生活事件':
                weight += 0.05
            if respondent_info.get('sleep') == '睡眠质量差':
                weight += 0.05
                
            final_score = total_score * weight
            
            # 构建详细的解释
            explanation = f"""评估结果：
- 情绪表达：{scores.get('情绪表达', 0)}分
- 认知水平：{scores.get('认知水平', 0)}分
- 应对方式：{scores.get('应对方式', 0)}分
- 社会支持：{scores.get('社会支持', 0)}分
- 风险程度：{scores.get('风险程度', 0)}分
基础总分：{total_score:.1f}分"""

            if weight > 1.0:
                explanation += f"\n加权后总分：{final_score:.1f}分（因风险因素调整）"
                
            return final_score, explanation
            
        except Exception as e:
            print(f"解析评分结果时出错: {str(e)}")
            print(f"API响应内容: {api_response}")
            return 50.0, "评分解析失败，使用默认分数"
            
    except Exception as e:
        print(f"API请求失败: {str(e)}")
        return 50.0, "API请求失败，使用默认分数"


@app.route('/start_chat', methods=['POST'])
def start_visit():
    data = request.json
    name = data.get('name')
    age = data.get('age')
    gender = data.get('gender')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id FROM respondents WHERE name=? AND age=? AND gender=?',
        (name, age, gender)
    )
    row = cursor.fetchone()
    if row:
        respondent_id = row[0]
    else:
        cursor.execute(
            'INSERT INTO respondents (name, age, gender, first_visit_date) VALUES (?, ?, ?, ?)',
            (name, age, gender, datetime.now())
        )
        conn.commit()
        cursor.execute(
            'SELECT id FROM respondents WHERE name=? AND age=? AND gender=?',
            (name, age, gender)
        )
        respondent_id_row = cursor.fetchone()
        if respondent_id_row:
            respondent_id = respondent_id_row[0]
        else:
            conn.close()
            return jsonify({'message': 'respondent insert failed', 'code': 1}), 500

    # 新访问记录
    visit_count = cursor.execute(
        'SELECT COUNT(*) FROM visits WHERE respondent_id=?', (respondent_id,)
    ).fetchval()
    visit_number = visit_count + 1
    cursor.execute(
        'INSERT INTO visits (respondent_id, visit_number, visit_date) VALUES (?, ?, ?)',
        (respondent_id, visit_number, datetime.now())
    )
    conn.commit()
    cursor.execute('SELECT MAX(id) FROM visits WHERE respondent_id=?', (respondent_id,))
    chat_id_row = cursor.fetchone()
    if chat_id_row:
        chat_id = chat_id_row[0]
    else:
        conn.close()
        return jsonify({'message': 'visit insert failed', 'code': 1}), 500

    conn.close()
    return jsonify({'message': 'success', 'code': 0, 'chat_id': chat_id})


@app.route('/get_questions', methods=['GET'])
def get_questions():
    """从数据库随机获取20个问题"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, question_text FROM questions')
    all_qs = cursor.fetchall()
    conn.close()

    # 随机抽取20条
    sampled = random.sample(all_qs, min(len(all_qs), 20))
    questions = [{'id': q[0], 'text': q[1]} for q in sampled]
    return jsonify({'code': 0, 'message': 'success', 'questions': questions})


@app.route('/answer_question', methods=['POST'])
def answer_question():
    """保存回答并由千问 API 评分"""
    data = request.json
    print("收到的 JSON：", data)
    print(" chat_id, q_id, answer：", data.get('chat_id'), data.get('q_id'), data.get('answer'))
    chat_id = data.get('chat_id')
    q_id = data.get('q_id')
    answer_text = data.get('answer')

    if not chat_id or not q_id or answer_text is None:
        return jsonify({'status': 'error', 'code': 400, 'message': '缺少参数'}), 400

    # 获取问题文本
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT question_text FROM questions WHERE id=?', (q_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({'status': 'error', 'code': 404, 'message': '问题未找到'}), 404
    question = row[0]

    # 获取受访者基本信息
    respondent_info = get_respondent_info(chat_id)

    # 调用千问评分
    try:
        score, explanation = evaluate_qa(question, answer_text, respondent_info)
        print("评分结果：", score)
    except Exception as e:
        return jsonify({'status': 'error', 'code': 500, 'message': str(e)}), 500

    # 保存结果
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO responses (chat_id, q_id, question, answer, score, response_time) VALUES (?, ?, ?, ?, ?, ?)',
        (chat_id, q_id, question, answer_text, score, datetime.now())
    )
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'code': 0, 'score': score, 'explanation': explanation})


@app.route('/query_depression', methods=['POST'])
def query_depression():
    data = request.json
    chat_id = data.get('chat_id')
    print('【DEBUG】chat_id:', chat_id)

    # 获取数据库连接和游标
    conn = get_db()
    cursor = conn.cursor()

    try:
        # 查询受访者基本信息
        cursor.execute(
            'SELECT r.* FROM respondents r JOIN visits v ON r.id = v.respondent_id WHERE v.id = ?',
            (chat_id,)
        )
        respondent_row = cursor.fetchone()
        if respondent_row is None:
            return jsonify({'message': 'respondent not found', 'code': 1}), 404

        # 获取受访者信息
        respondent_columns = [col[0] for col in cursor.description]
        respondent_info = dict(zip(respondent_columns, respondent_row))

        # 查询该 chat_id 的所有答题记录
        cursor.execute(
            'SELECT * FROM responses WHERE chat_id = ?',
            (chat_id,)
        )
        resp_rows = cursor.fetchall()
        resp_columns = [col[0] for col in cursor.description]
        responses = [dict(zip(resp_columns, r)) for r in resp_rows]

        print('【DEBUG】responses:', responses)

        # 计算总分
        total_score = 0
        valid_scores = 0
        for r in responses:
            try:
                score = float(r['score'])
                print(f"【DEBUG】单题分数: {score}")
                if score > 0:  # 只计算有效分数
                    total_score += score
                    valid_scores += 1
            except (ValueError, TypeError) as e:
                print(f"【DEBUG】无效的分数值: {r['score']}, 错误: {str(e)}")
                continue

        print(f"【DEBUG】有效分数数量: {valid_scores}, 总分累加: {total_score}")
        if valid_scores > 0:
            total_score = (total_score / valid_scores) * 1.25  # 计算平均分并应用权重
        else:
            print("【DEBUG】警告：没有有效的分数记录")
            total_score = 0

        print(f"【DEBUG】最终总分: {total_score}")

        depression_threshold = 1000
        has_depression = total_score >= depression_threshold

        # 构建答题记录字符串
        response_records = []
        for r in responses:
            record = f"问题：{r['question']}\n回答：{r['answer']}\n得分：{r['score']}"
            response_records.append(record)
        response_text = "\n".join(response_records)

        # 构建千问分析提示词
        analysis_prompt = (
            "作为一位专业的心理咨询师，请对以下评估结果进行分析并给出建议：\n\n"
            "来访者基本信息：\n"
            f"- 年龄：{respondent_info.get('age', '未知')}\n"
            f"- 性别：{respondent_info.get('gender', '未知')}\n"
            f"- 教育程度：{respondent_info.get('education', '未知')}\n"
            f"- 婚姻状况：{respondent_info.get('marital', '未知')}\n"
            f"- 慢性病史：{respondent_info.get('chronic', '未知')}\n"
            f"- 家族史：{respondent_info.get('family_history', '未知')}\n"
            f"- 生活事件：{respondent_info.get('life_event', '未知')}\n"
            f"- 睡眠状况：{respondent_info.get('sleep', '未知')}\n"
            f"- 运动情况：{respondent_info.get('exercise', '未知')}\n"
            f"- 烟酒情况：{respondent_info.get('smoke_drink', '未知')}\n"
            f"- 当前情绪：{respondent_info.get('mood', '未知')}\n\n"
            "评估结果：\n"
            f"总分：{total_score:.1f}分\n"
            f"是否达到抑郁阈值：{'是' if has_depression else '否'}\n\n"
            "详细答题记录：\n"
            f"{response_text}\n\n"
            "请从以下几个方面进行分析：\n"
            "1. 总体评估：根据总分和答题情况，给出整体心理状态评估\n"
            "2. 风险因素：分析可能存在的风险因素\n"
            "3. 保护因素：分析来访者的积极因素和资源\n"
            "4. 具体建议：给出针对性的建议，包括：\n"
            "   - 是否需要专业心理咨询\n"
            "   - 日常生活中的改善建议\n"
            "   - 社会支持系统的建议\n"
            "   - 其他具体可行的建议\n\n"
            "请用专业但易懂的语言输出分析结果。"
        )

        # 调用千问API进行分析
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {QIANWEN_API_KEY}"
        }
        
        data = {
            "model": "qwen-turbo",
            "messages": [
                {"role": "system", "content": "你是一位专业的心理咨询师，擅长分析心理评估结果并给出专业建议。"},
                {"role": "user", "content": analysis_prompt}
            ]
        }
        
        try:
            response = requests.post(QIANWEN_API_URL, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            analysis = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            print(f"获取分析结果时出错: {str(e)}")
            analysis = "获取分析结果失败，请稍后重试。"

        print(f"【DEBUG】返回前的sum_score: {total_score}")

        return jsonify({
            'message': 'success',
            'code': 0,
            'userInfo': respondent_info,
            'data': responses,
            'sum_score': round(total_score, 1),
            'has_depression': has_depression,
            'analysis': analysis
        })

    except Exception as e:
        print(f"处理评估结果时出错: {str(e)}")
        return jsonify({'message': f'处理评估结果时出错: {str(e)}', 'code': 1}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/start', methods=['GET', 'POST'])
def start():
    if request.method == 'POST':
        # 获取表单数据
        name = request.form.get('name')
        age = request.form.get('age')
        gender = request.form.get('gender')
        education = request.form.get('education')
        marital = request.form.get('marital')
        job = request.form.get('job')
        residence = request.form.get('residence')
        chronic = request.form.get('chronic')
        family_history = request.form.get('family_history')
        life_event = request.form.get('life_event')
        sleep = request.form.get('sleep')
        exercise = request.form.get('exercise')
        smoke_drink = request.form.get('smoke_drink')
        mood = request.form.get('mood')

        # 你可以选择保存到数据库，或存入 session，或直接传递到下一个页面
        # 下面以保存到 session 为例（如需存数据库可继续扩展）
        user_info = {
            'name': name,
            'age': age,
            'gender': gender,
            'education': education,
            'marital': marital,
            'job': job,
            'residence': residence,
            'chronic': chronic,
            'family_history': family_history,
            'life_event': life_event,
            'sleep': sleep,
            'exercise': exercise,
            'smoke_drink': smoke_drink,
            'mood': mood
        }
        # 这里可以保存到数据库，或 session
        # session['user_info'] = user_info

        # 跳转到下一个页面（如问卷页面）
        return redirect(url_for('get_questions'))  # 你可以根据实际流程调整

    return render_template('start.html')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/questions')
def questions():
    return render_template('questions.html')


def get_respondent_info(chat_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT r.* FROM respondents r JOIN visits v ON r.id = v.respondent_id WHERE v.id = ?',
        (chat_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


@app.route('/results')
def results():
    return render_template('results.html')


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
