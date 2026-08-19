# Things that can still go wrong

- **The DM-sending worker can stop while the website still looks healthy.** Render can still show the service as healthy because the website itself is running. Comments will be saved in the database, but no DMs will be sent until the service is restarted. The DMs are not deleted, but they may wait forever without someone noticing.

- **After five failed tries, the system gives up.** If PseudoGram or the internet keeps failing, the system tries the DM five times. After that, it marks the DM as `failed` and does not try again by itself. Someone would need to find that failed job and retry it manually.

- **`sent` does not fully prove that the person received the DM.** It only means PseudoGram answered successfully when we asked it to send the DM. If PseudoGram accepts the request but loses the DM later, our app will still show `sent`. Also, if the internet cuts off while sending, we ask PseudoGram to avoid sending twice by using the same safety ID. If PseudoGram does not remember that safety ID correctly, the retry could send a duplicate DM.

- **The duplicate number is not the full picture, and fake webhook calls are possible.** The `duplicates_blocked` number only counts one specific kind of duplicate: the same rule trying to DM the same user again. It does not count repeated copies of the exact same event, even though the app safely ignores them. Also, this Part A version does not check whether a webhook really came from PseudoGram. Someone who finds the public webhook URL could submit a fake comment, which could send an unwanted DM or stop the real user from receiving one later.
