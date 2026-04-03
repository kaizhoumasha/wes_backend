"""
Locust 负载测试脚本

使用方法：
1. 安装 locust: pip install locust
2. 运行测试: locust -f tests/load/locustfile.py
3. 访问 Web UI: http://localhost:8089
"""

import json
import random
import threading
import time

from locust import HttpUser, between, events, task

# 全局用户 ID 池（用于雪花 ID 模式）
EXISTING_USER_IDS = []
user_ids_lock = threading.Lock()

# 测试数据
TEST_USERNAMES = [f"test_user_{i}" for i in range(1, 1001)]
TEST_EMAILS = [f"user{i}@example.com" for i in range(1, 1001)]


class APIUser(HttpUser):
    """
    API 用户行为模拟

    模拟真实用户的使用场景：
    1. 浏览用户列表
    2. 查看用户详情
    3. 创建新用户
    4. 更新用户信息
    5. 删除用户
    """

    # 等待时间：1-3 秒之间
    wait_time = between(1, 3)

    def on_start(self):
        """用户启动时的初始化操作"""
        # 先访问健康检查，确保服务可用
        self.client.get("/api/v1/admin/performance/health")

    @task(5)
    def get_user_list(self):
        """
        获取用户列表（高频操作，权重=5）

        测试缓存效果和分页性能
        """
        page = random.randint(1, 10)
        page_size = random.choice([10, 20, 50])

        with self.client.get(
            f"/api/v1/users?page={page}&page_size={page_size}",
            name="/api/v1/users (列表)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                # 验证响应格式
                try:
                    data = response.json()
                    if "items" not in data or "total" not in data:
                        response.failure("响应格式错误")
                except json.JSONDecodeError:
                    response.failure("响应不是有效的 JSON")
            elif response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")

    @task(3)
    def get_user_detail(self):
        """
        获取用户详情（中频操作，权重=3）

        测试缓存命中率和热点数据访问
        """
        # 如果没有可用的用户 ID，跳过测试
        if not EXISTING_USER_IDS:
            self.client.get("/api/v1/admin/performance/health", name="跳过详情查询（无可用ID）")
            return

        # 随机获取用户 ID（偏向前面的 ID，模拟热点数据）
        # 限制访问范围以提高缓存命中率
        if random.random() < 0.7 and len(EXISTING_USER_IDS) >= 10:
            # 70% 概率访问热点数据（前 10 个用户）
            user_id = random.choice(EXISTING_USER_IDS[:10])
        else:
            # 30% 概率访问其他数据（限制在前 30 个用户内）
            max_index = min(30, len(EXISTING_USER_IDS))
            user_id = random.choice(EXISTING_USER_IDS[:max_index])

        with self.client.get(
            f"/api/v1/users/{user_id}",
            name="/api/v1/users/:id (详情)",
            catch_response=True,
        ) as response:
            if response.status_code == 404:
                # 用户不存在是正常的
                response.success()
            elif response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    def create_user(self):
        """
        创建新用户（低频操作，权重=1）

        测试写操作性能和缓存一致性
        """
        username = f"load_test_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        email = f"{username}@example.com"

        user_data = {
            "username": username,
            "email": email,
            "full_name": f"Load Test {random.randint(1, 100)}",
            "password": "test_password_123",
        }

        with self.client.post(
            "/api/v1/users",
            json=user_data,
            name="/api/v1/users (创建)",
            catch_response=True,
        ) as response:
            if response.status_code in {201, 200}:
                # 验证用户是否创建成功
                try:
                    data = response.json()
                    if "id" in data:
                        # 保存创建的用户 ID，用于后续测试
                        if not hasattr(self, "created_user_ids"):
                            self.created_user_ids = []
                        self.created_user_ids.append(data["id"])

                        # 添加到全局 ID 池
                        with user_ids_lock:
                            if data["id"] not in EXISTING_USER_IDS:
                                EXISTING_USER_IDS.append(data["id"])

                        response.success()
                    else:
                        response.failure("响应中没有用户 ID")
                except json.JSONDecodeError:
                    response.failure("响应不是有效的 JSON")
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    def update_user(self):
        """
        更新用户信息（低频操作，权重=1）

        测试更新操作和缓存删除
        """
        user_id = None

        # 优先使用自己创建的用户
        if hasattr(self, "created_user_ids") and self.created_user_ids:
            user_id = random.choice(self.created_user_ids)
        # 其次使用全局 ID 池
        elif EXISTING_USER_IDS:
            user_id = random.choice(EXISTING_USER_IDS)

        if user_id:
            update_data = {"full_name": f"Updated {int(time.time())}"}

            with self.client.put(
                f"/api/v1/users/{user_id}",
                json=update_data,
                name="/api/v1/users/:id (更新)",
                catch_response=True,
            ) as response:
                if response.status_code not in [200, 404]:
                    response.failure(f"HTTP {response.status_code}")
        else:
            # 没有可用的用户 ID，跳过
            self.client.get("/api/v1/admin/performance/health", name="跳过更新操作（无可用ID）")

    @task(1)
    def get_performance_metrics(self):
        """
        获取性能指标（低频操作，权重=1）

        监控系统性能
        """
        with self.client.get(
            "/api/v1/performance/metrics",
            name="/api/v1/performance/metrics",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")

    @task(2)
    def get_user_detail_nonexistent(self):
        """
        获取不存在的用户（中低频操作，权重=2）

        测试缓存穿透防护
        """
        # 使用明确不存在的大 ID（雪花 ID 范围之外）
        user_id = 9999999999999999 + random.randint(0, 1000)

        with self.client.get(
            f"/api/v1/users/{user_id}",
            name="/api/v1/users/:id (不存在)",
            catch_response=True,
        ) as response:
            if response.status_code == 404:
                # 正常返回 404
                response.success()
            else:
                response.failure(f"应该返回 404，实际返回 {response.status_code}")


class ReadUser(HttpUser):
    """
    只读用户

    模拟只读场景，用于测试缓存性能
    """

    wait_time = between(0.5, 2)

    @task(10)
    def get_user_list(self):
        """只访问用户列表"""
        page = random.randint(1, 5)
        self.client.get(f"/api/v1/users?page={page}&page_size=20")

    @task(10)
    def get_user_detail(self):
        """只访问用户详情（限制范围以提高缓存命中率）"""
        if EXISTING_USER_IDS:
            # 只访问前 20 个用户，提高缓存命中率
            max_index = min(20, len(EXISTING_USER_IDS))
            user_id = random.choice(EXISTING_USER_IDS[:max_index])
            self.client.get(f"/api/v1/users/{user_id}")
        else:
            # 没有可用的用户 ID，访问健康检查
            self.client.get("/api/v1/admin/performance/health")

    @task(1)
    def get_health(self):
        """健康检查"""
        self.client.get("/api/v1/admin/performance/health")


class WriteUser(HttpUser):
    """
    写入用户

    模拟写操作场景，用于测试写性能和缓存一致性
    """

    wait_time = between(2, 5)

    @task(5)
    def create_user(self):
        """创建用户"""
        username = f"write_test_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        user_data = {
            "username": username,
            "email": f"{username}@example.com",
            "password": "test_password_123",
        }
        response = self.client.post("/api/v1/users", json=user_data)

        # 将新创建的用户 ID 添加到全局池
        if response.status_code in [200, 201]:
            try:
                data = response.json()
                if "id" in data:
                    with user_ids_lock:
                        if data["id"] not in EXISTING_USER_IDS:
                            EXISTING_USER_IDS.append(data["id"])
            except json.JSONDecodeError:
                pass

    @task(3)
    def update_user(self):
        """更新随机用户"""
        if EXISTING_USER_IDS:
            user_id = random.choice(EXISTING_USER_IDS)
            # 更新用户的其他字段（不再使用 is_active）
            update_data = {"full_name": f"User {random.randint(1, 1000)}"}
            self.client.put(f"/api/v1/users/{user_id}", json=update_data)
        else:
            # 没有可用的用户 ID，访问健康检查
            self.client.get("/api/v1/admin/performance/health")


# 测试事件处理
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """测试开始时的操作"""
    print("\n" + "=" * 50)
    print("负载测试开始")
    print("=" * 50 + "\n")

    # 获取实际存在的用户 ID（适配雪花 ID 模式）
    print("正在获取实际用户 ID...")
    try:
        import requests

        base_url = environment.host or "http://localhost:8000"

        # 获取前 5 页用户（约 100 个 ID）
        for page in range(1, 6):
            try:
                response = requests.get(
                    f"{base_url}/api/v1/users",
                    params={"page": page, "page_size": 20},
                    timeout=5,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("items"):
                        user_ids = [user["id"] for user in data["items"]]
                        with user_ids_lock:
                            EXISTING_USER_IDS.extend(user_ids)
                        print(f"  第 {page} 页: 获取 {len(user_ids)} 个用户 ID")
                    else:
                        break
                else:
                    print(f"  第 {page} 页: HTTP {response.status_code}")
                    break
            except Exception as e:
                print(f"  第 {page} 页获取失败: {e}")
                break

        if EXISTING_USER_IDS:
            print(f"✓ 成功获取 {len(EXISTING_USER_IDS)} 个用户 ID")
            print(f"  ID 范围示例: {EXISTING_USER_IDS[0]} ~ {EXISTING_USER_IDS[-1]}")
        else:
            print("⚠️  警告: 未获取到任何用户 ID，部分测试可能失败")
    except Exception as e:
        print(f"⚠️  获取用户 ID 失败: {e}")

    print("=" * 50 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """测试结束时的操作"""
    print("\n" + "=" * 50)
    print("负载测试结束")

    # 输出统计信息
    if environment.stats.total.fail_ratio > 0.05:
        print(f"⚠️  警告: 失败率过高 ({environment.stats.total.fail_ratio:.2%})")
    else:
        print(f"✓ 测试通过: 失败率 {environment.stats.total.fail_ratio:.2%}")

    print(f"总请求数: {environment.stats.total.num_requests}")
    print(f"平均响应时间: {environment.stats.total.avg_response_time:.0f}ms")
    print(f"中位数响应时间: {environment.stats.total.median_response_time:.0f}ms")
    print(f"RPS: {environment.stats.total.total_rps:.2f}")
    print("=" * 50 + "\n")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """
    请求监听器

    可以在这里添加自定义的请求处理逻辑
    """
    # 例如：记录慢请求
    if response_time > 1000:
        print(f"⚠️  慢请求: {name} - {response_time:.0f}ms")
