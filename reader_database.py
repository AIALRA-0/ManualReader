"""保留少量空闲连接，避免每次导航都因最后一个连接关闭而重复整理 WAL。"""
from pathlib import Path
from collections import OrderedDict
import atexit,sqlite3,threading

class DatabaseConnections:
    def __init__(self,limit=8):
        self.limit=limit;self.anchors=OrderedDict();self.lock=threading.RLock()

    @staticmethod
    def signature(path):
        stat=path.stat();return stat.st_dev,stat.st_ino

    def connect(self,path):
        path=Path(path).resolve();key=str(path)
        with self.lock:
            prior=self.anchors.get(key)
            if prior and(not path.exists()or prior[0]!=self.signature(path)):
                self.anchors.pop(key)[1].close();prior=None
            if prior:self.anchors.move_to_end(key)
            else:
                anchor=sqlite3.connect(path,timeout=15,check_same_thread=False)
                try:
                    anchor.execute('PRAGMA journal_mode=WAL')
                    anchor.execute('CREATE TABLE IF NOT EXISTS reader_state (id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)')
                    anchor.commit()
                except Exception:anchor.close();raise
                self.anchors[key]=(self.signature(path),anchor)
                while len(self.anchors)>self.limit:self.anchors.popitem(last=False)[1][1].close()
        # 每个请求仍有自己的事务；同步写盘和版本检查规则保持不变。
        return sqlite3.connect(path,timeout=15)

    def close(self):
        with self.lock:
            for _,connection in self.anchors.values():connection.close()
            self.anchors.clear()

DATABASES=DatabaseConnections()
atexit.register(DATABASES.close)
