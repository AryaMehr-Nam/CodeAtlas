<?php

declare(strict_types=1);

namespace SampleSaas\Worker\Services;

class NotificationService
{
    /**
     * @var list<array<string, mixed>>
     */
    private array $dispatched = [];

    /**
     * @return array<string, mixed>
     */
    public function sendPaymentFailureAlert(
        string $accountEmail,
        string $subscriptionId,
        int $amountCents,
        string $currency,
        string $reason,
    ): array {
        $formattedAmount = $this->formatCurrency($amountCents, $currency);
        $message = [
            'channel' => 'email',
            'template' => 'payment_failed',
            'recipient' => $accountEmail,
            'subscription_id' => $subscriptionId,
            'amount_display' => $formattedAmount,
            'reason' => $reason,
        ];
        $this->dispatched[] = $message;

        return $message;
    }

    /**
     * @return array<string, mixed>
     */
    public function sendSyncDigest(string $batchId, int $processedCount, int $failedCount): array
    {
        $digest = [
            'channel' => 'ops_webhook',
            'template' => 'billing_sync_digest',
            'batch_id' => $batchId,
            'processed' => $processedCount,
            'failed' => $failedCount,
        ];
        $this->dispatched[] = $digest;

        return $digest;
    }

    public function formatCurrency(int $amountCents, string $currency = 'USD'): string
    {
        return sprintf('%s %.2f', strtoupper($currency), $amountCents / 100);
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function getDispatchedMessages(): array
    {
        return $this->dispatched;
    }
}
