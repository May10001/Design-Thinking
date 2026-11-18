class BusinessError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, details: dict | None = None):
        super().__init__(code)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def not_found():
    return BusinessError('NOT_FOUND', '任务或通知不存在', 404)
