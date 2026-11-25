<?php

declare(strict_types=1);

namespace SampleSaas\Worker\Jobs;

use SampleSaas\Worker\Models\PaymentRecord;
use SampleSaas\Worker\Services\NotificationService;

class BillingSyncJob
{
    public function __construct(
        private readonly NotificationService $notifications = new NotificationService(),
    ) {
    }

    /**
     * @param list<array{
     *   id: string,
     *   subscription_id: string,
     *   account_email: string,
     *   amount_cents: int,
     *   currency?: string,
     *   settled: bool,
     *   failure_reason?: string
     * }> $pendingPayloads
     * @return array<string, mixed>
     */
    public function handle(string $batchId, array $pendingPayloads): array
    {
        $processed = 0;
        $failed = 0;
        $records = [];

        foreach ($pendingPayloads as $payload) {
            $record = new PaymentRecord(
                id: $payload['id'],
                subscriptionId: $payload['subscription_id'],
                accountEmail: $payload['account_email'],
                amountCents: $payload['amount_cents'],
                currency: $payload['currency'] ?? 'USD',
            );

            if ($payload['settled']) {
                $record->markSettled();
                $processed++;
            } else {
                $reason = $payload['failure_reason'] ?? 'gateway_declined';
                $record->markFailed($reason, $this->notifications);
                $failed++;
            }

            $records[] = $record->toArray();
        }

        $digest = $this->notifications->sendSyncDigest($batchId, $processed, $failed);

        return [
            'batch_id' => $batchId,
            'processed' => $processed,
            'failed' => $failed,
            'records' => $records,
            'digest' => $digest,
        ];
    }
}
