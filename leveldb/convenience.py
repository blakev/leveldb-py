#!/usr/bin/env python
#
# Copyright (C) 2012 Space Monkey, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

"""
    LevelDB Python interface via C-Types.
    http://code.google.com/p/leveldb-py/

    Missing still (but in progress):
      * custom comparators, filter policies, caches

    This interface requires nothing more than the leveldb shared object with
    the C api being installed.

    Now requires LevelDB 1.6 or newer.

    For most usages, you are likely to only be interested in the "DB" and maybe
    the "WriteBatch" classes for construction. The other classes are helper
    classes that you may end up using as part of those two root classes.

     * DBInterface - This class wraps a LevelDB. Created by either the DB or
            MemoryDB constructors
     * Iterator - this class is created by calls to DBInterface::iterator.
            Supports range requests, seeking, prefix searching, etc
     * WriteBatch - this class is a standalone object. You can perform writes
            and deletes on it, but nothing happens to your database until you
            write the writebatch to the database with DB::write
"""

__author__ = "JT Olds"
__email__ = "jt@spacemonkey.com"

import weakref
from collections import namedtuple


Row = namedtuple('Row', 'key value')


class Error(Exception):
    pass


class _MemorySafety(object):

    __slots__ = ["ref", "_referrers", "__weakref__"]

    def __init__(self, ref):
        self.ref = ref
        self._referrers = weakref.WeakValueDictionary()

    def addReferrer(self, referrer):
        if self._referrers is None:
            raise Exception("already closed")
        self._referrers[id(referrer)] = referrer

    def close(self):
        ref, self.ref = self.ref, None
        referrers, self._referrers = self._referrers, None
        if referrers is not None:
            for referrer in referrers.valuerefs():
                referrer = referrer()
                if referrer is not None:
                    referrer.close()
        if ref is not None:
            ref.close()

    __del__ = close


class Iterator(object):

    """This class is created by calling __iter__ or iterator on a DB interface
    """

    __slots__ = ["_prefix", "_impl", "_keys_only"]

    def __init__(self, impl, keys_only=False, prefix=None):
        self._impl = impl
        self._prefix = prefix
        self._keys_only = keys_only

    def valid(self):
        """Returns whether the iterator is valid or not

        @rtype: bool
        """
        ref = self._impl.ref
        prefix = self._prefix
        valid = ref.valid()
        if not valid or prefix is None:
            return valid
        key = ref.key()
        return key[:len(prefix)] == prefix

    def seekFirst(self):
        """
        Jump to first key in database

        @return: self
        @rtype: Iter
        """
        prefix = self._prefix
        if prefix is not None:
            self._impl.ref.seek(prefix)
        else:
            self._impl.ref.seekFirst()
        return self

    def seekLast(self):
        """
        Jump to last key in database

        @return: self
        @rtype: Iter
        """
        ref = self._impl.ref
        prefix = self._prefix
        # if we have no prefix or the last possible prefix of this length, just
        # seek to the last key in the db.
        if prefix is None or prefix == "\xff" * len(prefix):
            ref.seekLast()
            return self

        # we have a prefix. see if there's anything after our prefix.
        # there's probably a much better way to calculate the next prefix.
        hex_prefix = prefix.encode('hex')
        next_prefix = hex(long(hex_prefix, 16) + 1)[2:].rstrip("L")
        next_prefix = next_prefix.rjust(len(hex_prefix), "0")
        next_prefix = next_prefix.decode("hex").rstrip("\x00")
        ref.seek(next_prefix)
        if ref.valid():
            # there is something after our prefix. we're on it, so step back
            ref.prev()
        else:
            # there is nothing after our prefix, just seek to the last key
            ref.seekLast()
        return self

    def seek(self, key):
        """Move the iterator to key. This may be called after StopIteration,
        allowing you to reuse an iterator safely.

        @param key: Where to position the iterator.
        @type key: str

        @return: self
        @rtype: Iter
        """
        prefix = self._prefix
        if prefix is not None:
            key = prefix + key
        self._impl.ref.seek(key)
        return self

    def key(self):
        """Returns the iterator's current key. You should be sure the iterator
        is currently valid first by calling valid()

        @rtype: string
        """
        prefix = self._prefix
        key = self._impl.ref.key()
        if prefix is not None:
            return key[len(prefix):]
        return key

    def value(self):
        """Returns the iterator's current value. You should be sure the
        iterator is currently valid first by calling valid()

        @rtype: string
        """
        return self._impl.ref.val()

    def __iter__(self):
        return self

    def next(self):
        """Advances the iterator one step. Also returns the current value prior
        to moving the iterator

        @rtype: Row (namedtuple of key, value) if keys_only=False, otherwise
                string (the key)

        @raise StopIteration: if called on an iterator that is not valid
        """
        if not self.valid():
            raise StopIteration()
        if self._keys_only:
            rv = self.key()
        else:
            rv = Row(self.key(), self.value())
        self._impl.ref.next()
        return rv

    def prev(self):
        """Backs the iterator up one step. Also returns the current value prior
        to moving the iterator.

        @rtype: Row (namedtuple of key, value) if keys_only=False, otherwise
                string (the key)

        @raise StopIteration: if called on an iterator that is not valid
        """
        if not self.valid():
            raise StopIteration()
        if self._keys_only:
            rv = self.key()
        else:
            rv = Row(self.key(), self.value())
        self._impl.ref.prev()
        return rv

    def stepForward(self):
        """Same as next but does not return any data or check for validity"""
        self._impl.ref.next()

    def stepBackward(self):
        """Same as prev but does not return any data or check for validity"""
        self._impl.ref.prev()

    def range(self, start_key=None, end_key=None, start_inclusive=True,
            end_inclusive=False):
        """A generator for some range of rows"""
        if start_key is not None:
            self.seek(start_key)
            if not start_inclusive and self.key() == start_key:
                self._impl.ref.next()
        else:
            self.seekFirst()
        for row in self:
            if end_key is not None and (row.key > end_key or (
                    not end_inclusive and row.key == end_key)):
                break
            yield row

    def keys(self):
        while self.valid():
            yield self.key()
            self.stepForward()

    def values(self):
        while self.valid():
            yield self.value()
            self.stepForward()

    def close(self):
        self._impl.close()


class _OpaqueWriteBatch(object):

    """This is an opaque write batch that must be written to using the putTo
    and deleteFrom methods on DBInterface.
    """

    def __init__(self):
        self._puts = {}
        self._deletes = set()
        self._private = True

    def clear(self):
        self._puts = {}
        self._deletes = set()


class WriteBatch(_OpaqueWriteBatch):

    """This class is created stand-alone, but then written to some existing
    DBInterface
    """

    def __init__(self):
        _OpaqueWriteBatch.__init__(self)
        self._private = False

    def put(self, key, val):
        self._deletes.discard(key)
        self._puts[key] = val

    def delete(self, key):
        self._puts.pop(key, None)
        self._deletes.add(key)


def _makeDBFromImpl(impl, default_sync=False, default_verify_checksums=False,
                    default_fill_cache=True):
    return DBInterface(_MemorySafety(impl), allow_close=True,
                       default_sync=default_sync,
                       default_verify_checksums=default_verify_checksums,
                       default_fill_cache=default_fill_cache)


class DBInterface(object):

    """This class is created through a few different means:

    Initially, it can be created using either the DB() or MemoryDB()
    module-level methods. In almost every case, you want the DB() method.

    You can then get new DBInterfaces from an existing DBInterface by calling
    snapshot or scope.
    """

    __slots__ = ["_impl", "_prefix", "_allow_close", "_default_sync",
                 "_default_verify_checksums", "_default_fill_cache"]

    def __init__(self, impl, prefix=None, allow_close=False,
                 default_sync=False, default_verify_checksums=False,
                 default_fill_cache=True):
        self._impl = impl
        self._prefix = prefix
        self._allow_close = allow_close
        self._default_sync = default_sync
        self._default_verify_checksums = default_verify_checksums
        self._default_fill_cache = default_fill_cache

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        if self._allow_close:
            self._impl.close()

    def newBatch(self):
        return _OpaqueWriteBatch()

    def put(self, key, val, sync=None):
        if sync is None:
            sync = self._default_sync
        prefix = self._prefix
        if prefix is not None:
            key = prefix + key
        self._impl.ref.put(key, val, sync=sync)

    def putTo(self, batch, key, val):
        if not batch._private:
            raise ValueError("batch not from DBInterface.newBatch")
        prefix = self._prefix
        if prefix is not None:
            key = prefix + key
        batch._deletes.discard(key)
        batch._puts[key] = val

    def delete(self, key, sync=None):
        if sync is None:
            sync = self._default_sync
        prefix = self._prefix
        if prefix is not None:
            key = prefix + key
        self._impl.ref.delete(key, sync=sync)

    def deleteFrom(self, batch, key):
        if not batch._private:
            raise ValueError("batch not from DBInterface.newBatch")
        prefix = self._prefix
        if prefix is not None:
            key = prefix + key
        batch._puts.pop(key, None)
        batch._deletes.add(key)

    def get(self, key, verify_checksums=None, fill_cache=None):
        if verify_checksums is None:
            verify_checksums = self._default_verify_checksums
        if fill_cache is None:
            fill_cache = self._default_fill_cache
        prefix = self._prefix
        if prefix is not None:
            key = prefix + key
        return self._impl.ref.get(key, verify_checksums=verify_checksums,
                fill_cache=fill_cache)

    def write(self, batch, sync=None):
        if sync is None:
            sync = self._default_sync
        prefix = self._prefix
        if prefix is not None and not batch._private:
            unscoped_batch = _OpaqueWriteBatch()
            for key, value in batch._puts.iteritems():
                unscoped_batch._puts[prefix + key] = value
            for key in batch._deletes:
                unscoped_batch._deletes.add(prefix + key)
            batch = unscoped_batch
        return self._impl.ref.write(batch, sync=sync)

    def iterator(self, verify_checksums=None, fill_cache=None, prefix=None,
                 keys_only=False):
        if verify_checksums is None:
            verify_checksums = self._default_verify_checksums
        if fill_cache is None:
            fill_cache = self._default_fill_cache
        orig_prefix = self._prefix
        if orig_prefix is not None:
            if prefix is None:
                prefix = orig_prefix
            else:
                prefix = orig_prefix + prefix
        impl = self._impl
        it = _MemorySafety(impl.ref.iterator(
                verify_checksums=verify_checksums, fill_cache=fill_cache))
        impl.addReferrer(it)
        return Iterator(it, keys_only=keys_only, prefix=prefix)

    def snapshot(self, default_sync=None, default_verify_checksums=None,
                 default_fill_cache=None):
        if default_sync is None:
            default_sync = self._default_sync
        if default_verify_checksums is None:
            default_verify_checksums = self._default_verify_checksums
        if default_fill_cache is None:
            default_fill_cache = self._default_fill_cache
        impl = self._impl
        snapshot = _MemorySafety(impl.ref.snapshot())
        impl.addReferrer(snapshot)
        return DBInterface(snapshot, prefix=self._prefix,
                allow_close=True, default_sync=default_sync,
                default_verify_checksums=default_verify_checksums,
                default_fill_cache=default_fill_cache)

    def __iter__(self):
        return self.iterator().seekFirst()

    def __getitem__(self, k):
        v = self.get(k)
        if v is None:
            raise KeyError(k)
        return v

    def __setitem__(self, k, v):
        self.put(k, v)

    def __delitem__(self, k):
        self.delete(k)

    def __contains__(self, key):
        return self.has(key)

    def has(self, key, verify_checksums=None, fill_cache=None):
        return self.get(key, verify_checksums=verify_checksums,
                fill_cache=fill_cache) is not None

    def scope(self, prefix, default_sync=None, default_verify_checksums=None,
                 default_fill_cache=None):
        if default_sync is None:
            default_sync = self._default_sync
        if default_verify_checksums is None:
            default_verify_checksums = self._default_verify_checksums
        if default_fill_cache is None:
            default_fill_cache = self._default_fill_cache
        orig_prefix = self._prefix
        if orig_prefix is not None:
            prefix = orig_prefix + prefix
        return DBInterface(self._impl, prefix=prefix, allow_close=False,
                           default_sync=default_sync,
                           default_verify_checksums=default_verify_checksums,
                           default_fill_cache=default_fill_cache)

    def range(self, start_key=None, end_key=None, start_inclusive=True,
            end_inclusive=False, verify_checksums=None, fill_cache=None):
        if verify_checksums is None:
            verify_checksums = self._default_verify_checksums
        if fill_cache is None:
            fill_cache = self._default_fill_cache
        return self.iterator(verify_checksums=verify_checksums,
                fill_cache=fill_cache).range(start_key=start_key,
                        end_key=end_key, start_inclusive=start_inclusive,
                        end_inclusive=end_inclusive)

    def keys(self, verify_checksums=None, fill_cache=None, prefix=None):
        if verify_checksums is None:
            verify_checksums = self._default_verify_checksums
        if fill_cache is None:
            fill_cache = self._default_fill_cache
        return self.iterator(verify_checksums=verify_checksums,
                fill_cache=fill_cache, prefix=prefix).seekFirst().keys()

    def values(self, verify_checksums=None, fill_cache=None, prefix=None):
        if verify_checksums is None:
            verify_checksums = self._default_verify_checksums
        if fill_cache is None:
            fill_cache = self._default_fill_cache
        return self.iterator(verify_checksums=verify_checksums,
                fill_cache=fill_cache, prefix=prefix).seekFirst().values()

    def approximateDiskSizes(self, *ranges):
        return self._impl.ref.approximateDiskSizes(*ranges)

    def compactRange(self, start_key, end_key):
        return self._impl.ref.compactRange(start_key, end_key)
