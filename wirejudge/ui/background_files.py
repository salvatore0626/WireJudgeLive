"""Sequential file jobs keep SQLite backups and imports off the UI thread."""
import queue
import threading

class FileWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True);self.tasks=queue.Queue();self.results=queue.SimpleQueue();self.stopping=threading.Event()
    def request(self,kind,function,*args):
        if not self.stopping.is_set():self.tasks.put((kind,function,args))
    def stop(self):self.stopping.set();self.tasks.put(None)
    def run(self):
        while True:
            task=self.tasks.get()
            if task is None:return
            kind,function,args=task
            try:self.results.put({'kind':kind,'value':function(*args)})
            except Exception as exc:self.results.put({'kind':kind,'error':str(exc)})
