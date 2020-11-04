"""Redis-based caching of per-Principal per-app access policy."""

import contextlib
import json
import logging
import pickle

from django.conf import settings
from redis import BlockingConnectionPool, exceptions
from redis.client import Redis

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name
_connection_pool = BlockingConnectionPool(
    max_connections=10, **settings.REDIS_CACHE_CONNECTION_PARAMS  # should match gunicorn.threads
)


class TenantCache:
    def __init__(self):
        self._connection = None

    @property
    def connection(self):
        """Get Redis connection from the pool."""
        if not self._connection:
            self._connection = Redis(connection_pool=_connection_pool)
            try:
                self._connection.ping()
            except exceptions.RedisError:
                self._connection = None
                raise
        return self._connection

    def key_for(self, schema_name):
        """Redis key for a given tenant."""
        return f"rbac::tenant::schema={schema_name}"

    def get(self, schema_name):
        """Get the tenant by schema_name."""
        try:
            tenant = self.connection.get(self.key_for(schema_name))
            if tenant:
                return pickle.loads(tenant)
        except exceptions.RedisError:
            logger.exception(f"Error querying tenant {schema_name}")
        return None

    def save(self, tenant):
        """Write the tenant for a request to Redis."""
        key = self.key_for(tenant.schema_name)
        try:
            logger.info(f"Caching tenant {key}")
            with self.connection.pipeline() as pipe:
                pipe.set(key, pickle.dumps(tenant))
                pipe.expire(key, settings.ACCESS_CACHE_LIFETIME)
                pipe.execute()
        except exceptions.RedisError:
            logger.exception(f"Error writing tenant cache for {key}")
        finally:
            try:
                pipe.reset()
            except:  # noqa: E722
                pass

    def delete(self, tenant):
        """Purge the given tenant from the cache."""
        key = self.key_for(tenant.schema_name)
        err_msg = f"Error deleting tenant cache {key}"
        with self.delete_handler(err_msg):
            logger.info(f"Deleting tenant cache {key}")
            self.connection.delete(key)

    @contextlib.contextmanager
    def delete_handler(self, err_msg):
        """Handle policy delete events."""
        if not settings.ACCESS_CACHE_ENABLED:
            return
        try:
            yield
        except exceptions.RedisError:
            logger.exception(err_msg)


class AccessCache:
    """Redis-based caching of per-Principal per-app access policy."""  # noqa: D204

    def __init__(self, tenant):
        """tenant: The name of the database schema for this tenant."""
        self.tenant = tenant
        self._connection = None

    @property
    def connection(self):
        """Get Redis connection from the pool."""
        if not self._connection:
            self._connection = Redis(connection_pool=_connection_pool)
            try:
                self._connection.ping()
            except exceptions.RedisError:
                self._connection = None
                raise
        return self._connection

    def key_for(self, uuid):
        """Redis key for a given user policy."""
        return f"rbac::policy::tenant={self.tenant}::user={uuid}"

    def get_policy(self, uuid, application):
        """Get the given user's policy for the given application."""
        if not settings.ACCESS_CACHE_ENABLED:
            return None
        try:
            policy_string = self.connection.hget(self.key_for(uuid), application)
            if policy_string:
                return json.loads(policy_string)
        except exceptions.RedisError:
            logger.exception("Error querying policy for uuid %s", uuid)
        return None

    @contextlib.contextmanager
    def delete_handler(self, err_msg):
        """Handle policy delete events."""
        if not settings.ACCESS_CACHE_ENABLED:
            return
        try:
            yield
        except exceptions.RedisError:
            logger.exception(err_msg)

    def delete_policy(self, uuid):
        """Purge the given user's policy from the cache."""
        err_msg = f"Error deleting policy for uuid {uuid}"
        with self.delete_handler(err_msg):
            logger.info("Deleting policy cache for uuid %s", uuid)
            self.connection.delete(self.key_for(uuid))

    def delete_all_policies_for_tenant(self):
        """Purge users' policies for a given tenant from the cache."""
        err_msg = f"Error deleting all policies for tenant {self.tenant}"
        with self.delete_handler(err_msg):
            logger.info("Deleting entire policy cache for tenant %s", self.tenant)
            keys = self.connection.keys(self.key_for("*"))
            if keys:
                self.connection.delete(*keys)

    def save_policy(self, uuid, application, policy):
        """Write the policy for a given user for a given app to Redis."""
        if not settings.ACCESS_CACHE_ENABLED:
            return
        try:
            logger.info("Caching policy for uuid %s", uuid)
            with self.connection.pipeline() as pipe:
                pipe.hset(self.key_for(uuid), application, json.dumps(policy))
                pipe.expire(self.key_for(uuid), settings.ACCESS_CACHE_LIFETIME)
                pipe.execute()
        except exceptions.RedisError:
            logger.exception("Error writing policy for uuid %s", uuid)
        finally:
            try:
                pipe.reset()
            except:  # noqa: E722
                pass
