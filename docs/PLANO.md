# Plano de melhorias da Trama

Resultado da análise do código (feita após a migração para o editor em passos) e plano de implementação em quatro fases.
Este arquivo é a especificação de trabalho: cada item tem um número que os commits e os testes citam.

## Diagnóstico

**O que está sólido:** isolamento do Python em contêiner (a parte mais valiosa e mais testada); modelo de fluxo v2 (gatilho, passos
aninhados, conteúdo dinâmico tipado, "executar após", retentativas); verificador antes de executar; 280 testes de backend, 27 e2e com axe.

**Onde está fraco (com evidência no código):**

| Área | Problema | Onde |
|---|---|---|
| Latência do Python | Cada passo Python inicia um contêiner (≈1 s). Um `Para cada` com 50 itens e um passo Python dentro paga 50 inícios. Não há pool aquecido. | `backend/app/sandbox/executor.py` (semáforo de 4, `docker run` por chamada) |
| Acompanhamento da execução | O editor faz *polling* a cada 300 ms em vez de receber eventos. | `frontend/src/hooks/useExecucao.ts` (`acompanharExecucao`; o polling de 300 ms está em `lib/acompanhamento.ts`) |
| Só um gatilho | "Acionar manualmente" é o único gatilho; sem Recorrência a Trama é um executor de scripts com interface, não uma automação. | `backend/app/blocks/builtin.py` |
| Escrever Python é cru | Sem autocompletar das entradas declaradas, sem lint antes de rodar, sem "testar só este passo" com os dados da última execução. | `CodeEditorInterno.tsx`, `PainelPasso.tsx` |
| Só biblioteca padrão | Sem `pandas`, `numpy`, `dateutil`… | `executor/Dockerfile`, `executor/runner.py` |
| Lacunas de passos | Sem *Switch*, filtrar/ordenar lista, datas, atraso, e sem expressão Python curta dentro de um campo (hoje é passo inteiro ou nada). | `blocks/builtin.py` |
| Ergonomia do designer | Sem arrastar para reordenar, recolher contêineres, copiar/colar passos entre fluxos, tema escuro. | `Designer.tsx`, `theme.css` |
| Histórico sem limite | Sem retenção nem filtro por estado. | `store.py`, `api.py` |
| Engenharia | Sem CI, sem lint/tipos, bundle de 640 kB num chunk só, `engine.py` com 762 linhas e `EditorPage.tsx` com 545. Camada nova sem revisão de segurança independente. | raiz |

## Itens

### A. Python como protagonista
1. **Expressão Python inline** nos campos: além de "valor fixo" e "conteúdo dinâmico", um modo `ƒ` onde se escreve `len(itens) * 2`
   ou `nome.title()` usando as fichas como variáveis. Todas as expressões de um passo são avaliadas numa única chamada ao contêiner.
2. **Pool aquecido de contêineres**: N contêineres pré-iniciados esperando no `stdin`; cada um atende um único trabalho e morre
   ("um contêiner por execução" continua valendo, só some o ≈1 s). Em laços, opção de executar o corpo em lote quando é só Python.
3. **Imagem com bibliotecas curadas** (`pandas`, `numpy`, `python-dateutil`, `tabulate`…) como imagem opcional separada; rede continua desligada.
4. **Editor de código melhor**: autocompletar de `inputs["…"]`/`params["…"]` a partir das entradas declaradas; lint (`pyflakes`) antes de testar.
5. **Testar só este passo** com as entradas da última execução.
6. **Ver como código**: o fluxo inteiro traduzido para um script Python legível (gatilho → função, condição → `if`, para cada → `for`, escopo → `try/except`).

### B. Automação de verdade
7. **Gatilho Recorrência** (a cada N minutos/horas, horário fixo, dias da semana), agendador no processo da API, "Próximas execuções".
8. **Gatilho "Requisição HTTP recebida"** (webhook local), só em `127.0.0.1` por padrão.
9. **Passo Atraso** e **passo Switch**.
10. **Operações de dados**: Filtrar lista, Ordenar lista, Juntar/Dividir texto, Data e hora.

### C. Operação
11. **Retenção do histórico** (`TRAMA_RETENCAO_DIAS`, padrão 30) + filtro por estado.
12. **Eventos em vez de polling** (SSE); o polling fica como reserva.
13. **Backup/exportação completa** (projetos, blocos e histórico) em um zip.

### D. Ergonomia do designer
14. Arrastar para reordenar, **recolher/expandir** contêineres, **copiar/colar** passos entre fluxos (JSON na área de transferência).
15. **Tema escuro** (`prefers-color-scheme` + seleção manual), mantendo os contrastes AA.
16. Saídas reais da última execução no cartão do passo.

### E. Engenharia e segurança
17. **CI** (GitHub Actions) + lint (ruff, pyright, ESLint).
18. **Divisão de código**: CodeMirror em chunk próprio.
19. **Refatoração** de `engine.py` e `EditorPage.tsx` sem mudar comportamento.
20. **Revisão de segurança** da camada nova (conteúdo dinâmico, expressões inline, webhook, agendador).

## Fases

- **Fase 0 — Fundação:** 17, 18, 19. *Pronto quando:* CI verde, `make lint` existe, bundle inicial < 300 kB.
- **Fase 1 — Python protagonista:** 2 → 1 → 3 → 4/5 → 6 (nesta ordem; cada entrega destrava a seguinte).
  *Pronto quando:* um laço de 50 itens com Python dentro roda em < 5 s; os 6 exemplos têm versão "ver como código" que reproduz o resultado.
- **Fase 2 — Automação de verdade:** 11 → 12 → 7 → 8/9/10. *Pronto quando:* um fluxo agendado roda sozinho, aparece no histórico e o histórico respeita a retenção.
- **Fase 3 — Ergonomia e acabamento:** 14, 15, 16, 13, 20, README e capturas.

## Riscos e decisões

- **Expressão inline vs. custo:** cada passo com expressões custa uma chamada ao contêiner; só é aceitável com o pool aquecido (por isso o pool vem antes).
- **Agendador em processo único:** simples e coerente com a Trama de hoje, mas o fluxo só roda com a aplicação aberta.
- **Bibliotecas curadas aumentam a imagem** (≈400 MB com pandas/numpy): duas imagens, `trama-executor` (stdlib, padrão) e `trama-executor-dados`, escolhida por variável de ambiente.
- **Fora do escopo:** paralelismo e Switch com muitos ramos continuam fora; o modelo sequencial é o que mantém "executar após" e o histórico simples.
- **Regra inegociável:** o isolamento do Python não pode enfraquecer. Cada contêiner atende um único trabalho; `--network none`, `--read-only`,
  usuário sem privilégios e limites continuam; os testes de dentro do contêiner continuam passando sem alteração.
