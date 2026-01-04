"""
INSTANCE MANAGER - Enhanced instance management with portalocker
"""

import os
import json
import time
import socket
import hashlib
import logging
from datetime import datetime, timedelta

# Try to import portalocker
try:
    import portalocker
    PORTALOCKER_AVAILABLE = True
except ImportError:
    print("Warning: portalocker not installed. Install with: pip install portalocker")
    PORTALOCKER_AVAILABLE = False
    portalocker = None

logger = logging.getLogger(__name__)

# Import from our modules
from time_utils import get_nigeria_time

class EnhancedInstanceManager:
    """Enhanced instance manager with portalocker for robust file-based locking"""
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.instance_id = self.generate_instance_id()
        self.lock_file_path = "bot_instance.lock"
        self.lock_timeout = 300
        self.lock_held = False
        self.lock_file = None
        
        # Create lock directory if not exists
        os.makedirs(os.path.dirname(self.lock_file_path) or '.', exist_ok=True)
        
        logger.info(f"Instance Manager initialized: ID={self.instance_id}")
    
    def generate_instance_id(self) -> str:
        """Generate unique instance ID"""
        hostname = socket.gethostname()
        pid = os.getpid()
        timestamp = str(time.time())
        unique_string = f"{hostname}_{pid}_{timestamp}_{self.bot_token[-8:]}"
        return hashlib.md5(unique_string.encode()).hexdigest()[:12]
    
    def acquire_lock(self, timeout: int = 60) -> bool:
        """Acquire exclusive lock using portalocker"""
        if not PORTALOCKER_AVAILABLE:
            logger.warning("Portalocker not available, using fallback locking")
            return self.acquire_lock_fallback(timeout)
        
        try:
            if self.lock_held:
                logger.debug("Lock already held by this instance")
                return True
            
            # Try to acquire lock with portalocker
            self.lock_file = open(self.lock_file_path, 'w')
            
            try:
                # Try to acquire lock with timeout
                portalocker.lock(self.lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
                
                # Write instance info to lock file
                lock_data = {
                    'instance_id': self.instance_id,
                    'timestamp': get_nigeria_time().isoformat(),
                    'pid': os.getpid(),
                    'hostname': socket.gethostname(),
                    'expires': (get_nigeria_time() + timedelta(seconds=timeout)).isoformat()
                }
                
                self.lock_file.seek(0)
                self.lock_file.write(json.dumps(lock_data, indent=2))
                self.lock_file.truncate()
                self.lock_file.flush()
                
                self.lock_held = True
                self.lock_timeout = timeout
                
                logger.info(f"✅ Acquired exclusive lock: Instance {self.instance_id}")
                return True
                
            except portalocker.LockException:
                # Lock is already held by another process
                self.lock_file.close()
                self.lock_file = None
                
                # Read lock file to see who has the lock
                try:
                    with open(self.lock_file_path, 'r') as f:
                        lock_data = json.load(f)
                        lock_time = datetime.fromisoformat(lock_data.get('timestamp', '2000-01-01'))
                        
                        # Check if lock is stale
                        if get_nigeria_time() - lock_time > timedelta(seconds=timeout):
                            logger.warning(f"🔄 Stale lock detected, forcing acquisition...")
                            
                            # Force acquire by opening and locking
                            self.lock_file = open(self.lock_file_path, 'w')
                            portalocker.lock(self.lock_file, portalocker.LOCK_EX)
                            
                            # Write our instance info
                            lock_data = {
                                'instance_id': self.instance_id,
                                'timestamp': get_nigeria_time().isoformat(),
                                'pid': os.getpid(),
                                'hostname': socket.gethostname(),
                                'expires': (get_nigeria_time() + timedelta(seconds=timeout)).isoformat()
                            }
                            
                            self.lock_file.seek(0)
                            self.lock_file.write(json.dumps(lock_data, indent=2))
                            self.lock_file.truncate()
                            self.lock_file.flush()
                            
                            self.lock_held = True
                            self.lock_timeout = timeout
                            
                            logger.info(f"✅ Force acquired lock from stale holder: Instance {self.instance_id}")
                            return True
                        else:
                            logger.warning(f"⚠️ Lock held by instance: {lock_data.get('instance_id')} "
                                         f"(since {lock_time.strftime('%I:%M %p')})")
                            return False
                            
                except Exception as e:
                    logger.error(f"Error reading lock file: {e}")
                    return False
                    
        except Exception as e:
            logger.error(f"Acquire lock error: {e}")
            if self.lock_file:
                try:
                    self.lock_file.close()
                except:
                    pass
                self.lock_file = None
            return self.acquire_lock_fallback(timeout)
    
    def acquire_lock_fallback(self, timeout: int = 60) -> bool:
        """Fallback lock acquisition without portalocker"""
        try:
            if os.path.exists(self.lock_file_path):
                try:
                    with open(self.lock_file_path, 'r') as f:
                        lock_data = json.load(f)
                        lock_time = datetime.fromisoformat(lock_data.get('timestamp', '2000-01-01'))
                        
                        # Check if lock is stale
                        if get_nigeria_time() - lock_time > timedelta(seconds=timeout):
                            logger.warning("Stale lock detected in fallback mode")
                        else:
                            logger.warning(f"Lock held by instance: {lock_data.get('instance_id')}")
                            return False
                except:
                    pass
            
            # Acquire lock by writing our instance info
            lock_data = {
                'instance_id': self.instance_id,
                'timestamp': get_nigeria_time().isoformat(),
                'pid': os.getpid(),
                'hostname': socket.gethostname(),
                'expires': (get_nigeria_time() + timedelta(seconds=timeout)).isoformat()
            }
            
            with open(self.lock_file_path, 'w') as f:
                json.dump(lock_data, f, indent=2)
            
            self.lock_held = True
            self.lock_timeout = timeout
            logger.info(f"Acquired fallback lock: Instance {self.instance_id}")
            return True
            
        except Exception as e:
            logger.error(f"Fallback lock acquisition error: {e}")
            return False
    
    def renew_lock(self, timeout: int = 300):
        """Renew the lock to keep it active"""
        if not self.lock_held:
            return
        
        try:
            # Update lock file with new expiry
            lock_data = {
                'instance_id': self.instance_id,
                'timestamp': get_nigeria_time().isoformat(),
                'pid': os.getpid(),
                'hostname': socket.gethostname(),
                'expires': (get_nigeria_time() + timedelta(seconds=timeout)).isoformat()
            }
            
            if PORTALOCKER_AVAILABLE and self.lock_file:
                self.lock_file.seek(0)
                self.lock_file.write(json.dumps(lock_data, indent=2))
                self.lock_file.truncate()
                self.lock_file.flush()
            else:
                with open(self.lock_file_path, 'w') as f:
                    json.dump(lock_data, f, indent=2)
            
            logger.debug(f"🔁 Lock renewed for instance {self.instance_id}")
            
        except Exception as e:
            logger.error(f"Renew lock error: {e}")
            # Try to reacquire lock
            self.lock_held = False
            if self.lock_file:
                try:
                    self.lock_file.close()
                except:
                    pass
                self.lock_file = None
            self.acquire_lock(timeout)
    
    def release_lock(self):
        """Release the lock"""
        if not self.lock_held:
            return
        
        try:
            if PORTALOCKER_AVAILABLE and self.lock_file:
                portalocker.unlock(self.lock_file)
                self.lock_file.close()
                self.lock_file = None
            
            # Remove lock file
            try:
                os.remove(self.lock_file_path)
            except:
                pass
            
            self.lock_held = False
            
            logger.info(f"🔓 Released lock: Instance {self.instance_id}")
            
        except Exception as e:
            logger.error(f"Release lock error: {e}")
            self.lock_held = False
    
    def check_lock_status(self) -> dict:
        """Check current lock status"""
        try:
            if os.path.exists(self.lock_file_path):
                with open(self.lock_file_path, 'r') as f:
                    try:
                        lock_data = json.load(f)
                        lock_time = datetime.fromisoformat(lock_data.get('timestamp', ''))
                        expires = datetime.fromisoformat(lock_data.get('expires', ''))
                        
                        return {
                            'locked': True,
                            'instance_id': lock_data.get('instance_id'),
                            'holder': lock_data.get('hostname'),
                            'pid': lock_data.get('pid'),
                            'since': lock_time,
                            'expires': expires,
                            'is_expired': get_nigeria_time() > expires
                        }
                    except json.JSONDecodeError:
                        return {'locked': False, 'error': 'Invalid lock file'}
            return {'locked': False}
        except Exception as e:
            return {'locked': False, 'error': str(e)}
    
    def is_primary(self) -> bool:
        """Check if this instance is the primary (holds the lock)"""
        status = self.check_lock_status()
        if status.get('locked') and status.get('instance_id') == self.instance_id:
            return True
        return False
