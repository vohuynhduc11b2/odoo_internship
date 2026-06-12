-- disable oauth providers
UPDATE auth_oauth_custom_provider
   SET enabled = false;
