"""FAST 测试的持久化边界替身；退避逻辑使用真实 Repository。"""


class RetrySession:
    def __init__(self):
        self.rows = {}

    async def scalar(self, statement):
        values = statement.compile().params
        key = tuple(values[name] for name in ("workline_id_1", "scope_identity_1", "rack_id_1", "step_1"))
        return self.rows.get(key)

    def add(self, row):
        key = (row.workline_id, row.scope_identity, row.rack_id, row.step)
        assert key not in self.rows
        self.rows[key] = row

    async def flush(self):
        pass
