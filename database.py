import os
import json
import hashlib
import asyncio
import re
from datetime import datetime
from pathlib import Path

# ===========================
# File Cache
# ===========================
CACHE_FILE = os.getenv('CACHE_FILE', '/tmp/file_cache.json')
cache_lock = asyncio.Lock()

class FileCache:
    """
    مدیریت cache فایل‌ها
    - ذخیره URL → File ID
    - جلوگیری از دانلود مجدد
    """
    
    def __init__(self):
        self.cache = {}
        self.load()
    
    def _url_hash(self, url):
        """تولید hash برای URL"""
        # نرمال‌سازی URL - حذف query params
        normalized_url = re.sub(r'\?.*$', '', url)
        return hashlib.md5(normalized_url.encode()).hexdigest()
    
    def load(self):
        """بارگذاری cache از فایل"""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r') as f:
                    self.cache = json.load(f)
                print(f"✅ Cache loaded: {len(self.cache)} entries")
            else:
                self.cache = {}
                print("📝 New cache created")
        except Exception as e:
            print(f"⚠️ Cache load error: {e}")
            self.cache = {}
    
    def save(self):
        """ذخیره cache در فایل"""
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump(self.cache, f, indent=2)
            print(f"💾 Cache saved: {len(self.cache)} entries")
        except Exception as e:
            print(f"⚠️ Cache save error: {e}")
    
    async def get(self, url):
        """دریافت file_id از cache"""
        async with cache_lock:
            url_hash = self._url_hash(url)
            
            if url_hash in self.cache:
                entry = self.cache[url_hash]
                
                # بررسی اعتبار (30 روز)
                cached_time = datetime.fromisoformat(entry['cached_at'])
                now = datetime.now()
                days_old = (now - cached_time).days
                
                if days_old > 30:
                    print(f"⚠️ Cache expired for {url[:50]}...")
                    del self.cache[url_hash]
                    self.save()
                    return None
                
                print(f"✅ Cache HIT: {url[:50]}...")
                return entry
            
            print(f"❌ Cache MISS: {url[:50]}...")
            return None
    
    async def set(self, url, file_id, file_type, file_name, file_size):
        """ذخیره file_id در cache"""
        async with cache_lock:
            url_hash = self._url_hash(url)
            
            self.cache[url_hash] = {
                'url': url,
                'file_id': file_id,
                'file_type': file_type,
                'file_name': file_name,
                'file_size': file_size,
                'cached_at': datetime.now().isoformat()
            }
            
            self.save()
            print(f"💾 Cached: {file_name} ({file_id})")
    
    async def delete(self, url):
        """حذف از cache"""
        async with cache_lock:
            url_hash = self._url_hash(url)
            
            if url_hash in self.cache:
                del self.cache[url_hash]
                self.save()
                print(f"🗑️ Deleted from cache: {url[:50]}...")
                return True
            
            return False
    
    async def stats(self):
        """آمار cache"""
        async with cache_lock:
            total_size = sum(
                entry.get('file_size', 0) 
                for entry in self.cache.values()
            )
            
            return {
                'total_entries': len(self.cache),
                'total_size': total_size,
                'entries': list(self.cache.values())
            }

# نمونه سراسری
file_cache = FileCache()

# ===========================
# User History
# ===========================
USER_HISTORY_FILE = os.getenv('USER_HISTORY_FILE', '/tmp/user_history.json')
history_lock = asyncio.Lock()

class UserHistory:
    """ذخیره تاریخچه کاربران"""
    
    def __init__(self):
        self.history = {}
        self.load()
    
    def load(self):
        try:
            if os.path.exists(USER_HISTORY_FILE):
                with open(USER_HISTORY_FILE, 'r') as f:
                    self.history = json.load(f)
                print(f"✅ History loaded: {len(self.history)} users")
            else:
                self.history = {}
        except Exception as e:
            print(f"⚠️ History load error: {e}")
            self.history = {}
    
    def save(self):
        try:
            os.makedirs(os.path.dirname(USER_HISTORY_FILE), exist_ok=True)
            with open(USER_HISTORY_FILE, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"⚠️ History save error: {e}")
    
    async def add(self, user_id, url, file_id, file_name, file_size):
        """اضافه کردن به تاریخچه"""
        async with history_lock:
            user_id_str = str(user_id)
            
            if user_id_str not in self.history:
                self.history[user_id_str] = []
            
            # اضافه کردن entry جدید
            self.history[user_id_str].append({
                'url': url,
                'file_id': file_id,
                'file_name': file_name,
                'file_size': file_size,
                'timestamp': datetime.now().isoformat()
            })
            
            # نگه‌داشتن فقط 50 آخر
            if len(self.history[user_id_str]) > 50:
                self.history[user_id_str] = self.history[user_id_str][-50:]
            
            self.save()
    
    async def get_recent(self, user_id, limit=5):
        """دریافت لینک‌های اخیر کاربر"""
        async with history_lock:
            user_id_str = str(user_id)
            
            if user_id_str not in self.history:
                return []
            
            # جدیدترین‌ها اول
            recent = self.history[user_id_str][-limit:]
            recent.reverse()
            
            return recent

# نمونه سراسری
user_history = UserHistory()
