-- Initialize database with extensions and test database

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create test database if running in development
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tradingutils_test') THEN
        PERFORM dblink_exec('dbname=postgres', 'CREATE DATABASE tradingutils_test');
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        -- dblink may not be available, that's okay
        NULL;
END $$;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE tradingutils TO postgres;
