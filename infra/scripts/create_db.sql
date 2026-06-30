-- SEGURA MULTAS — DB + usuário dedicados no Postgres da VPS.
-- Rode como superusuário (postgres) na instância existente:
--   psql -h $POSTGRES_HOST -U postgres -f infra/scripts/create_db.sql
-- ATENÇÃO: troque a senha abaixo (ou injete por variável no seu fluxo).

-- 1) Papel da aplicação
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'seguramultas') THEN
      CREATE ROLE seguramultas WITH LOGIN PASSWORD 'TROQUE_ESTA_SENHA';
   END IF;
END
$$;

-- 2) Banco dedicado (UTF8). Locale não é forçado para evitar falha em containers
--    sem pt_BR.UTF-8; o encoding UTF8 já garante acentuação correta nos dados.
SELECT 'CREATE DATABASE seguramultas OWNER seguramultas ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'seguramultas')\gexec

-- 3) Privilégios no schema public (conectado ao banco seguramultas)
\connect seguramultas
ALTER SCHEMA public OWNER TO seguramultas;
GRANT ALL ON SCHEMA public TO seguramultas;

-- 4) Extensões úteis (UUID via gen_random_uuid, e-mail case-insensitive)
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- As tabelas serão criadas pelas migrações Alembic na M2.
