import paramiko
import schedule
import time
import os
import sys
from flask import Flask, jsonify, render_template_string
from threading import Thread, Event
import logging

app = Flask(__name__)

vps_status = {}
flask_shutdown_event = Event()

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger()

def get_vps_configs():
    configs = []
    index = 1
    while True:
        hostname = os.environ.get(f'HOSTNAME_{index}')
        if not hostname:
            break
        
        config = {
            'index': index,
            'hostname': hostname,
            'username': os.environ.get(f'USERNAME_{index}'),
            'password': os.environ.get(f'PASSWORD_{index}'),
            'script_path': os.environ.get(f'SCRIPT_PATH_{index}')
        }
        configs.append(config)
        
        logger.info(f"Loaded VPS config {index}: {config['hostname']}, {config['username']}")
        
        index += 1
    return configs

def establish_ssh_connection(config, retries=3, delay=5):
    """尝试建立SSH连接，支持重试机制"""
    client = None
    attempt = 0
    while attempt < retries:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=config['hostname'],
                username=config['username'],
                password=config['password'],
                port=22
            )
            logger.info(f"Successfully connected to {config['hostname']} on attempt {attempt + 1}")
            return client
        except Exception as e:
            attempt += 1
            logger.error(f"Failed to connect to {config['hostname']} (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)
    logger.error(f"Failed to connect to {config['hostname']} after {retries} attempts.")
    return None

def check_and_run_script(config):
    logger.info(f"Checking VPS {config['index']}: {config['hostname']}")
    client = establish_ssh_connection(config)
    
    if client is None:
        vps_status[config['hostname']] = {
            'index': config['index'],
            'status': "Connection Failed",
            'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
            'username': config['username']
        }
        return
    
    try:
        script_path = config['script_path']
        script_name = os.path.basename(script_path)

        check_command = f"ps -eo args | grep {script_name} | grep -v grep"
        stdin, stdout, stderr = client.exec_command(check_command)
        output = stdout.read().decode('utf-8').strip()

        if output and script_path in output:
            status = "Running"
            logger.info(f"Script is running on {config['hostname']}")
        else:
            logger.info(f"Script not running on {config['hostname']}. Attempting to restart.")
            restart_command = f"nohup /bin/sh {script_path} > /dev/null 2>&1 &"
            stdin, stdout, stderr = client.exec_command(restart_command)
            status = "Restarted"
            logger.info(f"Script restarted on {config['hostname']}")

        vps_status[config['hostname']] = {
            'index': config['index'],
            'status': status,
            'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
            'username': config['username']
        }

    except Exception as e:
        logger.error(f"Error occurred while checking VPS {config['index']} - {config['hostname']}: {str(e)}")
        vps_status[config['hostname']] = {
            'index': config['index'],
            'status': f"Error: {str(e)}",
            'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
            'username': config['username']
        }
    finally:
        client.close()

def check_all_vps():
    logger.info("Starting VPS check")
    vps_configs = get_vps_configs()
    for config in vps_configs:
        check_and_run_script(config)
    
    table = "+---------+-----------------------+----------+-------------------------+----------+\n"
    table += "| Index   | Hostname              | Status   | Last Check              | Username |\n"
    table += "+---------+-----------------------+----------+-------------------------+----------+\n"
    
    for hostname, status in vps_status.items():
        table += "| {:<7} | {:<21} | {:<8} | {:<23} | {:<8} |\n".format(
            status['index'],
            hostname[:21],
            status['status'][:8],
            status['last_check'],
            status['username'][:8]
        )
        table += "+---------+-----------------------+----------+-------------------------+----------+\n"
    
    logger.info("\n" + table)

@app.route('/')
def index():
    html = '''
    <h1>VPS Status Overview</h1>
    <table border="1">
        <tr>
            <th>Index</th>
            <th>Hostname</th>
            <th>Status</th>
            <th>Last Check</th>
            <th>Username</th>
        </tr>
        {% for hostname, data in vps_status.items() %}
        <tr>
            <td>{{ data.index }}</td>
            <td><a href="/status/{{ hostname }}">{{ hostname }}</a></td>
            <td>{{ data.status }}</td>
            <td>{{ data.last_check }}</td>
            <td>{{ data.username }}</td>
        </tr>
        {% endfor %}
    </table>
    '''
    return render_template_string(html, vps_status=vps_status)

@app.route('/status/<hostname>')
def vps_status_detail(hostname):
    if hostname in vps_status:
        return jsonify(vps_status[hostname])
    else:
        return jsonify({"error": "VPS not found"}), 404

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "uptime": time.time() - start_time}), 200

def run_flask():
    logger.info("Starting Flask server")
    try:
        app.run(host='0.0.0.0', port=8080, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask server encountered an error: {e}")
    finally:
        logger.info("Flask server has stopped")

def main():
    global start_time
    start_time = time.time()

    logger.info("===== VPS monitoring script is starting =====")

    # 启动 Flask 服务器
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    
    vps_configs = get_vps_configs()
    logger.info(f"Found {len(vps_configs)} VPS configurations")

    logger.info("Running initial VPS check")
    check_all_vps()

    # 每小时执行一次
    schedule.every(1).hours.do(check_all_vps)
    logger.info("Scheduled VPS check every 1 hour")

    logger.info("===== VPS monitoring script is running =====")

    heartbeat_count = 0
    try:
        while not flask_shutdown_event.is_set():
            schedule.run_pending()
            time.sleep(60)
            heartbeat_count += 1
            if heartbeat_count % 5 == 0:
                logger.info(f"Heartbeat: Script is still running. Uptime: {heartbeat_count} minutes")
    except KeyboardInterrupt:
        logger.info("Received shutdown signal, stopping script...")
    finally:
        flask_shutdown_event.set()
        flask_thread.join()
        logger.info("VPS monitoring script has stopped.")

if __name__ == "__main__":
    main()
