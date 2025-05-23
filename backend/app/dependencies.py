from sqlalchemy.orm import Session
from app.database import get_db
from app.face_utils import FaceRecognition
from multiprocessing import Manager, cpu_count
import concurrent.futures
import multiprocessing
import time
import logging
from app.models import Employee

logger = logging.getLogger(__name__)

# Determine optimal number of workers based on CPU count
CPU_COUNT = cpu_count()
PROCESS_WORKERS = max(CPU_COUNT - 1, 1)  # Leave one CPU for system tasks
THREAD_WORKERS = CPU_COUNT * 2  # More threads than CPUs for I/O bound tasks

logger.info(f"System has {CPU_COUNT} CPUs, using {PROCESS_WORKERS} process workers and {THREAD_WORKERS} thread workers")

# Create a process pool for CPU-intensive tasks (face recognition)
process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=PROCESS_WORKERS)

# Create a thread pool for I/O bound tasks (database operations, network calls)
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=THREAD_WORKERS, thread_name_prefix="io_worker")

# Create multiprocessing queues
manager = Manager()
processing_results_queue = manager.Queue(maxsize=100)
websocket_responses_queue = manager.Queue(maxsize=100)

# Dictionary to store pending futures
pending_futures = {}

# Employee cache to avoid frequent database queries
employee_cache = manager.dict()
employee_cache_lock = manager.Lock()
employee_cache_last_updated = manager.Value('d', 0)
EMPLOYEE_CACHE_TTL = 300  # 5 minutes

# Dictionary to track number of pending tasks per client
client_pending_tasks = manager.dict()
client_pending_tasks_lock = manager.Lock()

# Store active WebSocket connections
active_connections = {}

# Initialize face recognition
face_recognition = FaceRecognition()

def get_face_recognition() -> FaceRecognition:
    return face_recognition

def get_process_pool():
    return process_pool

def get_thread_pool():
    return thread_pool

def get_queues():
    return processing_results_queue, websocket_responses_queue

def get_pending_futures():
    return pending_futures

def get_employee_cache():
    return employee_cache, employee_cache_lock, employee_cache_last_updated

def get_client_tasks():
    return client_pending_tasks, client_pending_tasks_lock

def get_active_connections():
    return active_connections

def get_cached_employees():
    """Get employees from cache or database with TTL"""
    current_time = time.time()
    with employee_cache_lock:
        if current_time - employee_cache_last_updated.value > EMPLOYEE_CACHE_TTL or not employee_cache:
            # Update cache
            employees = Employee().query()
            employee_cache.clear()
            employee_cache.update({employee["objectId"]: employee for employee in employees})
            employee_cache_last_updated.value = current_time
            logger.info("Employee cache updated")
        return list(employee_cache.values()) 