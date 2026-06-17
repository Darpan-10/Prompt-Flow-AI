terraform {

  required_version = ">= 1.6.0"

  required_providers {

    aws = {

      source  = "hashicorp/aws"
      version = "~> 5.0"

    }


  }


  backend "s3" {

    bucket         = "promptflow-terraform-state-ap"
    key            = "module2/email-worker/terraform.tfstate"
    region         = "ap-south-1"
    encrypt        = true
    dynamodb_table = "promptflow-terraform-locks"

  }


}

