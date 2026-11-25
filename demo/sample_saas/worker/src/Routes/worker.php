<?php

declare(strict_types=1);

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Route;
use SampleSaas\Worker\Jobs\BillingSyncJob;
use SampleSaas\Worker\Services\NotificationService;

Route::post('/worker/sync', function (Request $request): array {
    $batchId = (string) $request->input('batch_id', 'manual-sync');
    /** @var list<array<string, mixed>> $payloads */
    $payloads = (array) $request->input('payments', []);

    $notifier = new NotificationService();
    $job = new BillingSyncJob($notifier);

    return $job->handle($batchId, $payloads);
});

Route::get('/worker/health', function (): array {
    return [
        'status' => 'ok',
        'worker' => 'sample-saas-billing-sync',
    ];
});
