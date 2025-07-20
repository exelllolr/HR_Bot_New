CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    role VARCHAR(50) NOT NULL CHECK (role IN ('HR', 'Employer', 'Admin')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE vacancies (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    vacancy_data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE resumes (
    id SERIAL PRIMARY KEY,
    vacancy_id INTEGER REFERENCES vacancies(id),
    user_id BIGINT NOT NULL,
    resume_text TEXT NOT NULL,
    score FLOAT NOT NULL,
    analysis TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
