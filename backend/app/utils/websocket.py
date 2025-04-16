import asyncio
import logging
import concurrent.futures
from typing import Dict, Any
from fastapi import WebSocket
from ..dependencies import get_active_connections, get_queues, get_client_tasks, get_pending_futures

logger = logging.getLogger(__name__)

# Create a thread pool executor for I/O operations
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)

async def _send_message_to_client(websocket: WebSocket, message: Dict[str, Any], client_id: str = None) -> bool:
    """Send message to a client and return success status"""
    try:
        # Use the event loop to run the send operation in the thread pool
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(thread_pool, lambda: websocket.send_json(message))
        if client_id:
            logger.debug(f"Successfully sent message to client {client_id}")
        return True
    except Exception as e:
        if client_id:
            logger.error(f"Error sending message to client {client_id}: {str(e)}")
        return False

async def broadcast_attendance_update(attendance_data: Dict[str, Any]):
    """Broadcast attendance updates to all connected clients"""
    active_connections = get_active_connections()
    if not active_connections:
        logger.info("No active connections to broadcast to")
        return

    # Ensure objectId is included if this is a deletion
    if attendance_data.get("action") == "delete" and "objectId" not in attendance_data:
        logger.warning("Missing objectId in delete attendance update")
        if "attendance_id" in attendance_data:
            attendance_data["objectId"] = attendance_data["attendance_id"]

    # Create a message with the attendance update
    message = {
        "type": "attendance_update",
        "data": attendance_data
    }

    # Log the broadcast
    logger.info(f"Broadcasting attendance update to {len(active_connections)} clients: {attendance_data}")

    # Send to all connected clients using gather to process in parallel
    send_tasks = []
    for client_id, websocket in active_connections.items():
        send_tasks.append(_send_message_to_client(websocket, message, client_id))
    
    # Wait for all tasks to complete and get results
    results = await asyncio.gather(*send_tasks, return_exceptions=True)
    
    # Remove any disconnected clients
    disconnected_clients = [client_id for i, client_id in enumerate(active_connections.keys()) 
                           if isinstance(results[i], Exception) or results[i] is False]
    
    for client_id in disconnected_clients:
        if client_id in active_connections:
            del active_connections[client_id]
            logger.info(f"Removed disconnected client {client_id}. Total connections: {len(active_connections)}")

async def send_notification(websocket: WebSocket, message: str, notification_type: str = "info", client_id: str = None) -> bool:
    """Send a notification message to the client"""
    notification = {
        "type": "notification",
        "notification_type": notification_type,
        "message": message
    }
    return await _send_message_to_client(websocket, notification, client_id)

async def ping_client(websocket: WebSocket):
    """Send periodic ping messages to keep the connection alive"""
    try:
        while True:
            await asyncio.sleep(30)  # PING_INTERVAL
            success = await _send_message_to_client(websocket, {"type": "ping"})
            if not success:
                break
    except asyncio.CancelledError:
        logger.info("Ping task cancelled")
    except Exception as e:
        logger.error(f"Ping task error: {str(e)}")

async def process_queue():
    """Process the queue and broadcast updates to all connected clients"""
    processing_results_queue, _ = get_queues()
    while True:
        try:
            # Check if there are any items in the queue
            if not processing_results_queue.empty():
                # Get the next item from the queue
                item = processing_results_queue.get()

                # Process the item based on its type
                if item.get("type") == "attendance_update":
                    # Broadcast the attendance update
                    await broadcast_attendance_update(item.get("data", []))

                # Mark the task as done
                processing_results_queue.task_done()

            # Sleep for a short time to avoid busy waiting
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error processing queue: {str(e)}")
            # Sleep for a longer time if there was an error
            await asyncio.sleep(1)

async def process_websocket_responses():
    """Process the websocket responses queue and send responses to clients"""
    _, websocket_responses_queue = get_queues()
    active_connections = get_active_connections()
    
    while True:
        try:
            # Check if there are any items in the queue
            if not websocket_responses_queue.empty():
                # Get the next item from the queue
                item = websocket_responses_queue.get()
                client_id = item["client_id"]

                # Check if the client is still connected
                if client_id not in active_connections:
                    logger.info(f"Skipping response to disconnected client {client_id}")
                    websocket_responses_queue.task_done()
                    continue

                websocket = active_connections[client_id]

                # Check if this is an error response
                if "error" in item:
                    success = await _send_message_to_client(
                        websocket, 
                        {"status": "processing_error", "message": item["error"]},
                        client_id
                    )
                    # Send notification for error
                    await send_notification(websocket, f"Error processing: {item['error']}", "error", client_id)
                    if not success and client_id in active_connections:
                        del active_connections[client_id]
                    websocket_responses_queue.task_done()
                    continue

                # Process the results
                processed_users = item["processed_users"]
                attendance_updates = item["attendance_updates"]

                if not processed_users:
                    if item["no_face_count"] > 0:
                        # No face detected
                        success = await _send_message_to_client(
                            websocket,
                            {"status": "no_face_detected"},
                            client_id
                        )
                        # Send notification for no face detected
                        await send_notification(websocket, "No face detected in the image", "warning", client_id)
                        if not success and client_id in active_connections:
                            del active_connections[client_id]
                    else:
                        # No matching users found
                        success = await _send_message_to_client(
                            websocket,
                            {"status": "no_matching_users"},
                            client_id
                        )
                        # Send notification for no matching users
                        await send_notification(websocket, "No matching users found", "warning", client_id)
                        if not success and client_id in active_connections:
                            del active_connections[client_id]
                else:
                    # Send response with all processed users to the current client
                    success = await _send_message_to_client(
                        websocket,
                        {
                            "multiple_users": True,
                            "users": processed_users
                        },
                        client_id
                    )
                    
                    # Send notification for successful face detection
                    if len(processed_users) == 1:
                        user = processed_users[0]
                        notification_msg = f"Face detected: {user.get('name', 'Unknown')} (ID: {user.get('employee_id', 'Unknown')})"
                        await send_notification(websocket, notification_msg, "success", client_id)
                    else:
                        notification_msg = f"Multiple faces detected: {len(processed_users)} people identified"
                        await send_notification(websocket, notification_msg, "success", client_id)
                    
                    if not success and client_id in active_connections:
                        del active_connections[client_id]

                    # Add attendance updates to the queue for broadcasting
                    if attendance_updates:
                        await broadcast_attendance_update(attendance_updates)

                # Mark the task as done
                websocket_responses_queue.task_done()

            # Sleep for a short time to avoid busy waiting
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error processing websocket responses: {str(e)}")
            # Sleep for a longer time if there was an error
            await asyncio.sleep(1)

def handle_future_completion(future, client_id):
    """Handle the completion of a future from the process pool"""
    client_pending_tasks, client_pending_tasks_lock = get_client_tasks()
    pending_futures = get_pending_futures()
    processing_results_queue, websocket_responses_queue = get_queues()
    
    try:
        processed_users, attendance_updates, last_recognized_users, no_face_count = future.result()
        
        # Add objectId and id to attendance updates if missing
        if attendance_updates:
            for update in attendance_updates:
                if "objectId" not in update and "attendance_id" in update:
                    update["objectId"] = update["attendance_id"]
                if "id" not in update and "employee_id" in update:
                    update["id"] = update["employee_id"]

        # Decrement pending tasks counter
        with client_pending_tasks_lock:
            if client_id in client_pending_tasks:
                client_pending_tasks[client_id] = max(0, client_pending_tasks[client_id] - 1)

        # Put the results in the websocket responses queue
        websocket_responses_queue.put({
            "client_id": client_id,
            "processed_users": processed_users,
            "attendance_updates": attendance_updates,
            "last_recognized_users": last_recognized_users,
            "no_face_count": no_face_count
        })
        
        # Also put attendance updates in the processing results queue for broadcasting
        if attendance_updates:
            processing_results_queue.put({
                "type": "attendance_update",
                "data": attendance_updates
            })
            
    except Exception as e:
        logger.error(f"Error handling future completion: {str(e)}")
        # Decrement pending tasks counter even on error
        with client_pending_tasks_lock:
            if client_id in client_pending_tasks:
                client_pending_tasks[client_id] = max(0, client_pending_tasks[client_id] - 1)
        # Put error in the queue
        websocket_responses_queue.put({
            "client_id": client_id,
            "error": str(e)
        })
    finally:
        # Clean up the future from pending_futures
        for key, value in list(pending_futures.items()):
            if value == client_id:
                del pending_futures[key]
                break

# Function to gracefully shutdown the thread pool
async def shutdown_thread_pool():
    """Shutdown the thread pool gracefully"""
    logger.info("Shutting down WebSocket thread pool")
    thread_pool.shutdown(wait=True)
    logger.info("WebSocket thread pool shutdown complete") 