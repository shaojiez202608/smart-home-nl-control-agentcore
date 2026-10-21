# Open questions

Business facts or decisions that were not guessed. Each item lists what the prototype does meanwhile.

1. **Which actions are sensitive on the real platform?** The prototype treats `lock.unlock` and
   `security_system.disarm` as sensitive. Other candidates (garage door open, window open, oven on, camera
   privacy mode off) depend on the real device catalogue. Meanwhile: the policy table in
   `src/core/smarthome_core/policy.py` is the single place to extend.
2. **What counts as a valid second confirmation?** The prototype accepts a confirmation call on the
   authenticated app API. Whether a PIN, biometric step-up or a separate trusted device is required is a
   product and security decision.
3. **Should voice requests for sensitive actions be held or refused?** The prototype holds them and lists
   them in the app (a second confirmation is required either way). Some products may prefer to refuse on voice.
4. **Preference expiry and which parameters count as preferences.** Defaults: 90 days; brightness, colour
   temperature, target temperature, volume. Both are configurable; the right values need product input.
5. **CloudWatch Transaction Search.** AgentCore spans in CloudWatch need this account-level setting. The
   prototype does not enable it (it would change an existing account setting). The account owner decides;
   built-in AgentCore metrics, runtime logs and the orchestrator's own metrics work without it.
6. **Real platform API shape and authentication.** The simulated API uses an API key. A real integration
   needs the platform's actual authentication (OAuth client credentials, mTLS, SigV4 behind API Gateway) and an
   OpenAPI description of the three operations.
7. **Device certificates and IoT policies for speakers** are not provisioned; how speakers authenticate to
   AWS IoT Core on the real platform is to be confirmed.
8. **Real-model scene behaviour.** With the default model, "我要睡觉了" sometimes dims a light instead of
   switching it off. Whether that is acceptable, or scene presets should constrain the plan, is a product
   question; safety properties hold either way.
9. **API Gateway CloudWatch Logs role.** The stack enables API access and execution logs, which need the
   account-level API Gateway CloudWatch Logs role in the Region. The stack does not create it (account-level
   setting); `make preflight` stops if it is missing. The account owner decides whether to set it.
10. **Per-user identity towards the platform.** The agent's gateway calls use one M2M identity for all users
    and pass the user id as a tool argument (read-only tool). Whether the real platform can accept a per-user
    token (delegated OAuth) or needs a gateway interceptor is to be confirmed with the platform team.
11. **Sharing an alias between devices.** The prototype refuses an alias already remembered for another device.
    Whether a product wants group aliases ("卧室的灯" for several lights) is a product decision.
