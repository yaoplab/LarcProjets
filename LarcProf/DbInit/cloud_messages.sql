-- Messagerie LarcProf (cloud Supabase uniquement — pas de copie locale).
--
--   * message individuel : recipient_id renseigné (professeur <-> coordinateur, etc.)
--   * diffusion          : recipient_id NULL — réservée aux coordinateurs, lue par tout le personnel
--
-- Les messages sont des données à croissance libre (comme student_event) : INSERT autorisé,
-- exception au principe gabarit. Aucun DELETE applicatif : un message se masque (deleted_at).
-- Idempotent : peut être rejoué sans erreur.

CREATE TABLE IF NOT EXISTS larc_message (
    id           BIGSERIAL    PRIMARY KEY,
    sender_id    INTEGER      NOT NULL REFERENCES larcauth_aecuser(id),
    recipient_id INTEGER      REFERENCES larcauth_aecuser(id),   -- NULL = diffusion à tout le personnel
    subject      VARCHAR(200) NOT NULL DEFAULT '',
    body         TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    CONSTRAINT larc_message_body_not_blank CHECK (length(btrim(body)) > 0),
    CONSTRAINT larc_message_no_self CHECK (recipient_id IS NULL OR recipient_id <> sender_id)
);

-- Accusé de lecture par utilisateur (indispensable pour les diffusions : un seul message, N lecteurs).
CREATE TABLE IF NOT EXISTS larc_message_read (
    message_id BIGINT      NOT NULL REFERENCES larc_message(id) ON DELETE CASCADE,
    user_id    INTEGER     NOT NULL REFERENCES larcauth_aecuser(id),
    read_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_larc_message_recipient ON larc_message (recipient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_larc_message_sender    ON larc_message (sender_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_larc_message_broadcast ON larc_message (created_at DESC) WHERE recipient_id IS NULL;

-- Une diffusion ne peut être émise que par un coordinateur (garde-fou côté base, en plus de l'appli).
CREATE OR REPLACE FUNCTION larc_message_check_broadcast() RETURNS trigger AS $$
BEGIN
    IF NEW.recipient_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM larcauth_aecuser u
        WHERE u.id = NEW.sender_id AND u.is_active AND u.type_coordonator
    ) THEN
        RAISE EXCEPTION 'Seul un coordinateur peut envoyer un message à tous les utilisateurs'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_larc_message_check_broadcast ON larc_message;
CREATE TRIGGER trg_larc_message_check_broadcast
    BEFORE INSERT ON larc_message
    FOR EACH ROW EXECUTE FUNCTION larc_message_check_broadcast();
