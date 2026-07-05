aws_region  = "ap-south-1"
environment = "dev"

# No dedicated SSE-S3 access-log bucket exists yet -- access logging on
# the reports bucket stays disabled until one is provisioned. Fill in
# once available:
# access_log_bucket_id = "promptflow-access-logs-dev"

# Fill in once you have an ECR repo for Module 6's image:
# ecr_repository_url = "123456789.dkr.ecr.ap-south-1.amazonaws.com/promptflow-module6"

# Fill in from Module 1 once it issues RS256 JWTs:
# jwt_public_key = "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"

# Fill in once an ALB target group exists for Module 6:
# target_group_arn = "arn:aws:elasticloadbalancing:..."
# alb_listener_arn  = "arn:aws:elasticloadbalancing:..."
