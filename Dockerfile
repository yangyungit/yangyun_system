# 使用轻量级 Python 3.9
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 1. 先复制依赖清单
COPY requirements.txt .

# 2. 安装依赖 (关键修改：增加了 -i 清华源地址)
# 这样下载速度会飞快，且不会报错
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 复制所有代码到服务器
COPY . .

# 4. 赋予启动脚本权限
RUN chmod +x start.sh

# 5. 启动！
CMD ["./start.sh"]