<?php

declare(strict_types=1);

namespace SampleSaas\Worker\Models;

// Deliberate architectural layer violation: Data model imports a Service layer class directly.
use SampleSaas\Worker\Services\NotificationService;

class PaymentRecord
{
    public const STATUS_PENDING = 'pending';
    public const STATUS_SETTLED = 'settled';
    public const STATUS_FAILED = 'failed';

    public function __construct(
        public readonly string $id,
        public readonly string $subscriptionId,
        public readonly string $accountEmail,
        public readonly int $amountCents,
        public readonly string $currency = 'USD',
        private string $status = self::STATUS_PENDING,
        private int $attemptCount = 0,
        private ?string $lastError = null,
    ) {
    }

    public function getStatus(): string
    {
        return $this->status;
    }

    public function getAttemptCount(): int
    {
        return $this->attemptCount;
    }

    public function markSettled(): void
    {
        $this->status = self::STATUS_SETTLED;
        $this->attemptCount++;
        $this->lastError = null;
    }

    public function markFailed(string $reason, ?NotificationService $notifier = null): void
    {
        $this->status = self::STATUS_FAILED;
        $this->attemptCount++;
        $this->lastError = $reason;

        $service = $notifier ?? new NotificationService();
        $service->sendPaymentFailureAlert(
            accountEmail: $this->accountEmail,
            subscriptionId: $this->subscriptionId,
            amountCents: $this->amountCents,
            currency: $this->currency,
            reason: $reason,
        );
    }

    /**
     * @return array<string, int|string|null>
     */
    public function toArray(): array
    {
        return [
            'id' => $this->id,
            'subscription_id' => $this->subscriptionId,
            'account_email' => $this->accountEmail,
            'amount_cents' => $this->amountCents,
            'currency' => $this->currency,
            'status' => $this->status,
            'attempt_count' => $this->attemptCount,
            'last_error' => $this->lastError,
        ];
    }
}
