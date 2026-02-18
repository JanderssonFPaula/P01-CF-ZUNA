-- =============================================================
-- SCRIPT SQL - Controle Financeiro v2.0
-- Prefixo: P01CF_
-- Execute no SQL Editor do Supabase
-- =============================================================

-- TABELA: usuários
CREATE TABLE IF NOT EXISTS p01cf_usuarios (
    id            BIGSERIAL PRIMARY KEY,
    nome          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    senha         TEXT NOT NULL,
    data_cadastro TIMESTAMP DEFAULT NOW()
);

-- TABELA: contas (com user_id)
CREATE TABLE IF NOT EXISTS p01cf_contas (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES p01cf_usuarios(id) ON DELETE CASCADE,
    nome          TEXT NOT NULL,
    banco         TEXT NOT NULL,
    categoria     TEXT NOT NULL,
    saldo         DECIMAL(10,2) DEFAULT 0,
    cor           TEXT DEFAULT '#007bff',
    data_criacao  TIMESTAMP DEFAULT NOW()
);

-- TABELA: transações
CREATE TABLE IF NOT EXISTS p01cf_transacoes (
    id         BIGSERIAL PRIMARY KEY,
    conta_id   BIGINT REFERENCES p01cf_contas(id) ON DELETE CASCADE,
    tipo       TEXT NOT NULL,
    valor      DECIMAL(10,2) NOT NULL,
    descricao  TEXT,
    data       TIMESTAMP DEFAULT NOW()
);

-- TABELA: listas de compras (com user_id)
CREATE TABLE IF NOT EXISTS p01cf_listas_compras (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES p01cf_usuarios(id) ON DELETE CASCADE,
    nome             TEXT NOT NULL,
    data_criacao     TIMESTAMP DEFAULT NOW(),
    concluida        BOOLEAN DEFAULT FALSE,
    conta_id         BIGINT REFERENCES p01cf_contas(id),
    data_conclusao   TIMESTAMP
);

-- TABELA: itens da lista
CREATE TABLE IF NOT EXISTS p01cf_itens_lista (
    id          BIGSERIAL PRIMARY KEY,
    lista_id    BIGINT REFERENCES p01cf_listas_compras(id) ON DELETE CASCADE,
    descricao   TEXT NOT NULL,
    valor       DECIMAL(10,2) NOT NULL,
    quantidade  INTEGER DEFAULT 1
);

-- Habilitar RLS
ALTER TABLE p01cf_usuarios       ENABLE ROW LEVEL SECURITY;
ALTER TABLE p01cf_contas         ENABLE ROW LEVEL SECURITY;
ALTER TABLE p01cf_transacoes     ENABLE ROW LEVEL SECURITY;
ALTER TABLE p01cf_listas_compras ENABLE ROW LEVEL SECURITY;
ALTER TABLE p01cf_itens_lista    ENABLE ROW LEVEL SECURITY;

-- Políticas de acesso público (a autenticação é feita pelo Flask)
DROP POLICY IF EXISTS "Permitir tudo" ON p01cf_usuarios;
CREATE POLICY "Permitir tudo" ON p01cf_usuarios FOR ALL USING (true);

DROP POLICY IF EXISTS "Permitir tudo" ON p01cf_contas;
CREATE POLICY "Permitir tudo" ON p01cf_contas FOR ALL USING (true);

DROP POLICY IF EXISTS "Permitir tudo" ON p01cf_transacoes;
CREATE POLICY "Permitir tudo" ON p01cf_transacoes FOR ALL USING (true);

DROP POLICY IF EXISTS "Permitir tudo" ON p01cf_listas_compras;
CREATE POLICY "Permitir tudo" ON p01cf_listas_compras FOR ALL USING (true);

DROP POLICY IF EXISTS "Permitir tudo" ON p01cf_itens_lista;
CREATE POLICY "Permitir tudo" ON p01cf_itens_lista FOR ALL USING (true);

-- Índices para melhor performance
CREATE INDEX IF NOT EXISTS idx_contas_user    ON p01cf_contas(user_id);
CREATE INDEX IF NOT EXISTS idx_trans_conta    ON p01cf_transacoes(conta_id);
CREATE INDEX IF NOT EXISTS idx_listas_user    ON p01cf_listas_compras(user_id);
CREATE INDEX IF NOT EXISTS idx_itens_lista    ON p01cf_itens_lista(lista_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON p01cf_usuarios(email);
