# Requisitos e critérios de aceite

## Lotes
- Status: available, reserved, sold, blocked.
- Mudanças registram ator, timestamp, status anterior/novo e motivo.
- Reserva tem expiração; concorrência não pode permitir duas vendas/reservas incompatíveis.
- Inadimplência não é exposta no site público.

## Contratos e parcelas
- Entrada, cronograma, vencimentos e regras configuráveis.
- Persistir versão das condições da contratação.
- Recebimento parcial, alocação rastreável, estorno, renegociação e distrato sem apagar histórico.
- Multa, juros, correção e distrato precisam ser validados por profissional competente.

## Proprietário e comissões
- Permitir valor fixo parcelado, percentual ou regra contratual.
- Configurar base de cálculo (contrato assinado, entrada, recebido) e gatilhos.
- Distinguir apurado, devido, pago e estornado.

## Financeiro
- Distinguir previsto, vencido, liquidado, estornado e cancelado.
- Liquidação confirmada pelo PSP/banco ou baixa manual autorizada e auditada.
- Importação de extrato identifica duplicatas e exceções.

## Site e portal
- Site exibe apenas conteúdo publicável aprovado.
- Portal do comprador acessa apenas seus contratos/documentos.
- Proteger documentos com autorização e URLs temporárias.

## SaaS e segurança
- Testes comprovam tenant A não acessa tenant B.
- Testes de cálculos financeiros e concorrência de lotes.
- Backup restaurado com sucesso; HTTPS, monitoramento, alertas e resposta a incidentes.
